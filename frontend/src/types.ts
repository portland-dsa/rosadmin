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
}

export type GroupDetail = Group & {
  members: RosterMember[]
}

export type Session = {
  member: {
    id: string
    name: string
  }
}

export type SearchResult = {
  matches: Member[]
}

export type GroupUpdate = {
  id: string
  role: Role
  remove: boolean
}
