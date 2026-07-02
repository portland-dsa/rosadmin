import type { Group, GroupDetail, GroupUpdate, Session, SearchResult } from '../types'

/* The whole backend contract behind one interface. `mock` and `client` each
   implement it; `index` selects which one the app uses. */
export interface Api {
  getSession(): Promise<Session | null>
  beginLogin(): void
  logout(): Promise<void>
  getBodies(): Promise<Group[]>
  getBody(groupId: string): Promise<GroupDetail>
  searchMembers(email: string): Promise<SearchResult>
  updateMemberGroups(memberId: string, groups: GroupUpdate[]): Promise<void>
}
