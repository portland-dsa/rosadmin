import type { Api } from './contract'
import type {
  Group,
  GroupDetail,
  GroupUpdate,
  Member,
  Role,
  SearchResult,
  Session,
} from '../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

/* Raw response shapes as the backend sends them, before we map to our types. */
type AliveResponse = {
  alive: boolean
  member?: { id: string; name: string }
}

type BodyInfoResponse = {
  id: string
  name: string
  members: Record<string, { name: string; email: string; role: Role }>
}

type SearchResponse = {
  matches: Member[]
}

async function getSession(): Promise<Session | null> {
  const res = await fetch(`${BASE}/auth/alive`, { credentials: 'include' })
  // 401/403 is the normal "no session" answer. Any other non-ok status is a
  // real fault (backend down, a broken OAuth callback); we log it for development
  // especially while wiring up Discord login.
  if (!res.ok) {
    if (res.status !== 401 && res.status !== 403) {
      const detail = await res.text().catch(() => '')
      console.warn(`getSession: unexpected /auth/alive status ${res.status}`, detail)
    }
    return null
  }
  const data = (await res.json()) as AliveResponse
  if (!data.alive || !data.member) return null
  return { member: { id: data.member.id, name: data.member.name } }
}

function beginLogin(): void {
  window.location.href = `${BASE}/auth/login/discord`
}

async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, { method: 'POST', credentials: 'include' })
}

async function getBodies(): Promise<Group[]> {
  const res = await fetch(`${BASE}/bodies`, { credentials: 'include' })
  if (!res.ok) throw new Error(`Could not load your groups (${res.status}).`)
  return (await res.json()) as Group[]
}

async function getBody(groupId: string): Promise<GroupDetail> {
  const res = await fetch(`${BASE}/bodies/info`, {
    credentials: 'include',
    headers: { 'X-Chapter-Body': groupId },
  })
  if (res.status === 403) throw new Error('You do not have access to this group.')
  if (!res.ok) throw new Error(`Could not load this group (${res.status}).`)
  const data = (await res.json()) as BodyInfoResponse
  const members = Object.entries(data.members).map(([id, m]) => ({
    id,
    name: m.name,
    email: m.email,
    role: m.role,
  }))
  return { id: data.id, name: data.name, members }
}

async function searchMembers(email: string): Promise<SearchResult> {
  const res = await fetch(`${BASE}/members/search`, {
    credentials: 'include',
    headers: { 'X-Member-Email': email },
  })
  if (!res.ok) throw new Error(`Search failed (${res.status}).`)
  const data = (await res.json()) as SearchResponse
  return { matches: data.matches }
}

async function updateMemberGroups(memberId: string, groups: GroupUpdate[]): Promise<void> {
  const res = await fetch(`${BASE}/members/groups/update`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: memberId, groups }),
  })
  // 207 = some updates applied and some didn't; the frontend treats it as a failure.
  if (res.status === 207) throw new Error('The change could not be completed.')
  if (!res.ok) throw new Error(`The change failed (${res.status}).`)
}

export const httpApi: Api = {
  getSession,
  beginLogin,
  logout,
  getBodies,
  getBody,
  searchMembers,
  updateMemberGroups,
}
