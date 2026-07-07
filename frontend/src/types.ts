export type Role = 'leader' | 'member'

export type Member = {
  id: string
  name: string
  email: string
}

export type RosterMember = Member & {
  role: Role
}

export type Group = {
  id: string
  name: string
  bodyType: string
}

export type GroupDetail = Group & {
  members: RosterMember[]
}

export type Session = {
  displayName: string
}

/* Exact-email search resolves to a single addable member or a typed miss, so the
   leader knows who to chase. The miss reasons are the backend's own vocabulary. */
export type SearchMissReason =
  | 'dues_expired'
  | 'no_membership_status'
  | 'malformed'
  | 'not_found'

export type SearchOutcome =
  | { status: 'good_standing'; member: Member }
  | { status: SearchMissReason }
