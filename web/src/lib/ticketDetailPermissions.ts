type RoleLike = string | null | undefined

export function canCreateEmployeeTicket(role: RoleLike) {
  return role === 'user'
}

export function canUseTicketReplyComposer(role: string | null | undefined, waitingForUser: boolean) {
  return waitingForUser && role === 'user'
}

export function canSubmitTicketFeedback(role: RoleLike) {
  return role === 'user'
}

export function canSubmitReviewDecision(role: RoleLike) {
  return role === 'admin'
}

export function canViewExecutionTrace(role: RoleLike) {
  return role === 'developer'
}
