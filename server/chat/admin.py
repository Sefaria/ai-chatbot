"""
Admin configuration for chat models.
"""

# Admin is not included in INSTALLED_APPS for this lightweight setup.
# To enable admin, add 'django.contrib.admin' and 'django.contrib.auth'
# to INSTALLED_APPS in settings.py, then register models here:
#
# from django.contrib import admin
# from .models import ChatMessage, ChatSession
#
# @admin.register(ChatMessage)
# class ChatMessageAdmin(admin.ModelAdmin):
#     list_display = ['message_id', 'user_id', 'role', 'server_timestamp']
#     list_filter = ['role', 'status']
#     search_fields = ['message_id', 'user_id', 'session_id', 'content']
#
# @admin.register(ChatSession)
# class ChatSessionAdmin(admin.ModelAdmin):
#     list_display = ['session_id', 'user_id', 'message_count', 'last_activity']
#     search_fields = ['session_id', 'user_id']

