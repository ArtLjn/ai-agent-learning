export function canUseTicketReplyComposer(role: string | null | undefined, waitingForUser: boolean) {
  return waitingForUser && role === 'user'
}
