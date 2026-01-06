"""
Chat API views with Claude agent integration.
"""

import asyncio
import json
import logging
import os
import time
import queue
from datetime import datetime
from threading import Thread

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ChatMessage, ChatSession
from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    HistoryMessageSerializer,
)
from .agent import ClaudeAgentService, ConversationMessage, AgentProgressUpdate

logger = logging.getLogger('chat')

# Global agent service (initialized lazily)
_agent_service = None


def get_agent_service() -> ClaudeAgentService:
    """Get or create the agent service singleton."""
    global _agent_service
    if _agent_service is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        _agent_service = ClaudeAgentService(api_key=api_key)
    return _agent_service


def run_async(coro):
    """Run an async coroutine in a sync context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # If we're already in an async context, create a new loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


@api_view(['POST'])
def chat(request):
    """
    Handle incoming chat messages with Claude agent.
    
    POST /api/chat
    
    Request body:
    {
        "userId": "abc123",
        "sessionId": "sess_...",
        "messageId": "msg_...",
        "timestamp": "2026-01-05T08:12:34.000Z",
        "text": "User question here",
        "context": {
            "pageUrl": "...",
            "locale": "en",
            "clientVersion": "1.2.0"
        }
    }
    
    Response:
    {
        "messageId": "msg_reply_...",
        "sessionId": "sess_...",
        "timestamp": "2026-01-05T08:12:36.000Z",
        "markdown": "### Answer\nHere is **markdown**..."
    }
    """
    start_time = time.time()
    
    # Validate request
    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Invalid request', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    data = serializer.validated_data
    context = data.get('context', {})
    
    # Log incoming message
    logger.info(
        f"📨 user={data['userId']} session={data['sessionId'][:20]}... "
        f"msg={data['messageId'][:20]}... text={data['text'][:50]}..."
    )
    
    # Update or create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=data['sessionId'],
        defaults={
            'user_id': data['userId'],
            'last_activity': timezone.now(),
        }
    )
    
    # Save user message
    user_message = ChatMessage.objects.create(
        message_id=data['messageId'],
        session_id=data['sessionId'],
        user_id=data['userId'],
        role=ChatMessage.Role.USER,
        content=data['text'],
        client_timestamp=data['timestamp'],
        page_url=context.get('pageUrl', ''),
        locale=context.get('locale', ''),
        client_version=context.get('clientVersion', ''),
    )
    
    try:
        # Get conversation history for this session
        history_messages = ChatMessage.objects.filter(
            session_id=data['sessionId']
        ).order_by('server_timestamp')[:50]  # Last 50 messages for context
        
        # Build conversation for agent
        conversation = []
        for msg in history_messages:
            conversation.append(ConversationMessage(
                role=msg.role,
                content=msg.content
            ))
        
        # Get agent service and send message
        agent = get_agent_service()
        agent_response = run_async(
            agent.send_message(
                messages=conversation,
                session_id=data['sessionId'],
                user_id=data['userId']
            )
        )
        
        # Calculate total latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Save assistant response
        response_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=data['sessionId'],
            user_id=data['userId'],
            role=ChatMessage.Role.ASSISTANT,
            content=agent_response.content,
            latency_ms=latency_ms,
            llm_calls=agent_response.llm_calls,
            tool_calls_count=len(agent_response.tool_calls),
            tool_calls_data=agent_response.tool_calls,
            input_tokens=agent_response.input_tokens,
            output_tokens=agent_response.output_tokens,
            cache_creation_tokens=agent_response.cache_creation_tokens,
            cache_read_tokens=agent_response.cache_read_tokens,
            model_name='claude-sonnet-4-5-20250929',
        )
        
        # Link user message to response
        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=['response_message', 'latency_ms'])
        
        # Update session stats
        session.message_count = ChatMessage.objects.filter(
            session_id=data['sessionId']
        ).count()
        session.total_input_tokens = (session.total_input_tokens or 0) + agent_response.input_tokens
        session.total_output_tokens = (session.total_output_tokens or 0) + agent_response.output_tokens
        session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)
        session.save(update_fields=[
            'message_count', 'last_activity',
            'total_input_tokens', 'total_output_tokens', 'total_tool_calls'
        ])
        
        # Log response
        logger.info(
            f"📤 response={response_message.message_id[:20]}... "
            f"latency={latency_ms}ms tools={len(agent_response.tool_calls)} "
            f"tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
        )
        
        # Return response
        response_data = {
            'messageId': response_message.message_id,
            'sessionId': data['sessionId'],
            'timestamp': response_message.server_timestamp.isoformat(),
            'markdown': agent_response.content,
        }
        
        return Response(response_data)
    
    except Exception as e:
        logger.error(f"❌ Agent error: {e}", exc_info=True)
        
        # Save error response
        error_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=data['sessionId'],
            user_id=data['userId'],
            role=ChatMessage.Role.ASSISTANT,
            content="I'm sorry, I encountered an error processing your request. Please try again.",
            status=ChatMessage.Status.FAILED,
            latency_ms=int((time.time() - start_time) * 1000),
        )
        
        user_message.response_message = error_message
        user_message.save(update_fields=['response_message'])
        
        return Response({
            'messageId': error_message.message_id,
            'sessionId': data['sessionId'],
            'timestamp': error_message.server_timestamp.isoformat(),
            'markdown': error_message.content,
        })


@api_view(['POST'])
def chat_stream(request):
    """
    Handle incoming chat messages with streaming tool progress via SSE.
    
    POST /api/chat/stream
    
    Request body: Same as /api/chat
    
    Response: Server-Sent Events stream
    
    Event types:
    - event: progress
      data: {"type": "status|tool_start|tool_end", "text": "...", ...}
    
    - event: message
      data: {"messageId": "...", "sessionId": "...", "timestamp": "...", "markdown": "..."}
    
    - event: error
      data: {"error": "..."}
    """
    start_time = time.time()
    
    # Validate request
    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Invalid request', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    data = serializer.validated_data
    context = data.get('context', {})
    
    # Log incoming message
    logger.info(
        f"📨 [stream] user={data['userId']} session={data['sessionId'][:20]}... "
        f"msg={data['messageId'][:20]}... text={data['text'][:50]}..."
    )
    
    # Update or create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=data['sessionId'],
        defaults={
            'user_id': data['userId'],
            'last_activity': timezone.now(),
        }
    )
    
    # Save user message
    user_message = ChatMessage.objects.create(
        message_id=data['messageId'],
        session_id=data['sessionId'],
        user_id=data['userId'],
        role=ChatMessage.Role.USER,
        content=data['text'],
        client_timestamp=data['timestamp'],
        page_url=context.get('pageUrl', ''),
        locale=context.get('locale', ''),
        client_version=context.get('clientVersion', ''),
    )
    
    def generate_sse():
        """Generator that yields SSE events."""
        progress_queue = queue.Queue()
        result_holder = {'response': None, 'error': None}
        
        def on_progress(update: AgentProgressUpdate):
            """Callback for agent progress updates."""
            progress_queue.put(update)
        
        def run_agent():
            """Run the agent in a separate thread."""
            try:
                # Get conversation history
                history_messages = ChatMessage.objects.filter(
                    session_id=data['sessionId']
                ).order_by('server_timestamp')[:50]
                
                conversation = []
                for msg in history_messages:
                    conversation.append(ConversationMessage(
                        role=msg.role,
                        content=msg.content
                    ))
                
                agent = get_agent_service()
                result_holder['response'] = asyncio.run(
                    agent.send_message(
                        messages=conversation,
                        on_progress=on_progress,
                        session_id=data['sessionId'],
                        user_id=data['userId']
                    )
                )
            except Exception as e:
                result_holder['error'] = str(e)
            finally:
                # Signal completion
                progress_queue.put(None)
        
        # Start agent in background thread
        agent_thread = Thread(target=run_agent, daemon=True)
        agent_thread.start()
        
        # Stream progress events
        while True:
            try:
                update = progress_queue.get(timeout=60)  # 60s timeout
                
                if update is None:
                    # Agent finished
                    break
                
                # Format SSE event for progress
                event_data = {
                    'type': update.type,
                    'text': update.text,
                }
                
                if update.tool_name:
                    event_data['toolName'] = update.tool_name
                if update.tool_input:
                    event_data['toolInput'] = update.tool_input
                if update.description:
                    event_data['description'] = update.description
                if update.is_error is not None:
                    event_data['isError'] = update.is_error
                if update.output_preview:
                    event_data['outputPreview'] = update.output_preview
                
                yield f"event: progress\ndata: {json.dumps(event_data)}\n\n"
                
            except queue.Empty:
                # Send keepalive
                yield ": keepalive\n\n"
        
        # Wait for thread to complete
        agent_thread.join(timeout=5)
        
        # Handle result
        latency_ms = int((time.time() - start_time) * 1000)
        
        if result_holder['error']:
            logger.error(f"❌ [stream] Agent error: {result_holder['error']}")
            
            error_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=data['sessionId'],
                user_id=data['userId'],
                role=ChatMessage.Role.ASSISTANT,
                content="I'm sorry, I encountered an error processing your request.",
                status=ChatMessage.Status.FAILED,
                latency_ms=latency_ms,
            )
            
            user_message.response_message = error_msg
            user_message.save(update_fields=['response_message'])
            
            yield f"event: error\ndata: {json.dumps({'error': result_holder['error']})}\n\n"
            return
        
        agent_response = result_holder['response']
        
        # Save assistant response
        response_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=data['sessionId'],
            user_id=data['userId'],
            role=ChatMessage.Role.ASSISTANT,
            content=agent_response.content,
            latency_ms=latency_ms,
            llm_calls=agent_response.llm_calls,
            tool_calls_count=len(agent_response.tool_calls),
            tool_calls_data=agent_response.tool_calls,
            input_tokens=agent_response.input_tokens,
            output_tokens=agent_response.output_tokens,
            cache_creation_tokens=agent_response.cache_creation_tokens,
            cache_read_tokens=agent_response.cache_read_tokens,
            model_name='claude-sonnet-4-5-20250929',
        )
        
        # Link user message to response
        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=['response_message', 'latency_ms'])
        
        # Update session stats
        session.message_count = ChatMessage.objects.filter(
            session_id=data['sessionId']
        ).count()
        session.total_input_tokens = (session.total_input_tokens or 0) + agent_response.input_tokens
        session.total_output_tokens = (session.total_output_tokens or 0) + agent_response.output_tokens
        session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)
        session.save(update_fields=[
            'message_count', 'last_activity',
            'total_input_tokens', 'total_output_tokens', 'total_tool_calls'
        ])
        
        # Log response
        logger.info(
            f"📤 [stream] response={response_message.message_id[:20]}... "
            f"latency={latency_ms}ms tools={len(agent_response.tool_calls)} "
            f"tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
        )
        
        # Send final message event
        final_data = {
            'messageId': response_message.message_id,
            'sessionId': data['sessionId'],
            'timestamp': response_message.server_timestamp.isoformat(),
            'markdown': agent_response.content,
            'toolCalls': agent_response.tool_calls,
            'stats': {
                'llmCalls': agent_response.llm_calls,
                'inputTokens': agent_response.input_tokens,
                'outputTokens': agent_response.output_tokens,
                'latencyMs': latency_ms,
            }
        }
        
        yield f"event: message\ndata: {json.dumps(final_data)}\n\n"
    
    response = StreamingHttpResponse(
        generate_sse(),
        content_type='text/event-stream'
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # Disable nginx buffering
    
    return response


@api_view(['GET'])
def history(request):
    """
    Get conversation history.
    
    GET /api/history?userId=...&sessionId=...&before=...&limit=...
    
    Query parameters:
    - userId (required): User identifier
    - sessionId (required): Session identifier
    - before (optional): ISO timestamp, load messages before this time
    - limit (optional): Max messages to return (default 20, max 100)
    
    Response:
    {
        "messages": [...],
        "hasMore": true
    }
    """
    user_id = request.query_params.get('userId')
    session_id = request.query_params.get('sessionId')
    before = request.query_params.get('before')
    limit = min(int(request.query_params.get('limit', 20)), 100)
    
    if not user_id or not session_id:
        return Response(
            {'error': 'userId and sessionId are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    logger.info(f"📜 history user={user_id} session={session_id[:20]}... limit={limit}")
    
    # Build query
    queryset = ChatMessage.objects.filter(
        user_id=user_id,
        session_id=session_id,
    )
    
    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace('Z', '+00:00'))
            queryset = queryset.filter(server_timestamp__lt=before_dt)
        except ValueError:
            return Response(
                {'error': 'Invalid before timestamp'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    # Get messages (one extra to check if there's more)
    messages = list(
        queryset.order_by('-server_timestamp')[:limit + 1]
    )
    
    has_more = len(messages) > limit
    messages = messages[:limit]
    
    # Reverse to get chronological order
    messages.reverse()
    
    # Serialize
    serializer = HistoryMessageSerializer(messages, many=True)
    
    return Response({
        'messages': serializer.data,
        'hasMore': has_more,
    })


@api_view(['POST'])
def reload_prompt(request):
    """
    Reload the system prompt from Langfuse without restarting the server.
    
    POST /api/admin/reload-prompt
    
    Response:
    {
        "success": true,
        "promptLength": 12345
    }
    """
    try:
        agent = get_agent_service()
        prompt_length = agent.reload_prompt()
        
        logger.info(f"🔄 Prompt reloaded: {prompt_length} chars")
        
        return Response({
            'success': True,
            'promptLength': prompt_length,
        })
    except Exception as e:
        logger.error(f"❌ Failed to reload prompt: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
