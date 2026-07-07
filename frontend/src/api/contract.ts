import type { GroupDetail, RosterMember, SearchOutcome, Session } from '../types'

/* The whole backend contract behind one interface. `mock` and `client` each
   implement it; `index` selects which one the app uses. */
export interface Api {
  getSession(): Promise<Session | null>
  beginLogin(): Promise<void>
  logout(): Promise<void>
  getGroups(): Promise<GroupDetail[]>
  searchMember(email: string): Promise<SearchOutcome>
  addMember(groupId: string, memberId: string): Promise<RosterMember>
  removeMember(groupId: string, memberId: string): Promise<void>
}
