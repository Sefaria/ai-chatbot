/** Derive temporary notices from durable turn state, never from cached notice rows. */
export function withStoppedTurns(messages) {
  const source = messages.filter(message => message.role !== 'stopped');
  return source.flatMap(message => {
    if (message.role !== 'user' || message.processingState !== 'cancelled') return [message];
    return [message, {
      messageId: `${message.messageId}_stopped`,
      sessionId: message.sessionId,
      role: 'stopped',
      content: '',
      timestamp: message.timestamp,
      appetizerData: message.appetizerData || null,
      showNotice: true,
      noEntryAnimation: true
    }];
  });
}
