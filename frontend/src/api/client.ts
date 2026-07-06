import type { Api } from './contract'
import type { GroupDetail, Role, RosterMember, SearchOutcome, Session } from '../types'

/* Same-origin by default: the Vite dev server proxies /api to the backend, and
   in production the SPA is served from the API's own origin. An explicit base is
   only for setups that serve the two apart. */
const BASE = import.meta.env.VITE_API_BASE ?? ''

/* When the backend runs with ROSADMIN_FAKE_LOGIN=1, sign in as a fake persona
   instead of Discord. This is the only auth path available off the Linux SSO
   host, so it is how local (e.g. macOS) development authenticates. */
const FAKE_LOGIN = import.meta.env.VITE_FAKE_LOGIN === 'true'

/* Raw response shapes as the backend sends them, before we map to our types. */
type MeResponse = {
  display_name: string
}

type GroupMemberResponse = {
  id: string
  full_name: string
  email: string
  role: Role
}

type GroupResponse = {
  id: string
  name: string
  body_type: string
  members: GroupMemberResponse[]
}

type SearchResponse =
  | { status: 'good_standing'; member: { id: string; full_name: string; email: string } }
  | { status: 'dues_expired' | 'no_membership_status' | 'malformed' | 'not_found' }

function toRosterMember(m: GroupMemberResponse): RosterMember {
  return { id: m.id, name: m.full_name, email: m.email, role: m.role }
}

async function getSession(): Promise<Session | null> {
  // Any non-200 means "no readable session": logged out (401/403), or, while the
  // backend's read endpoints are stubbed, a 501. Both land on the login screen. A
  // missing or malformed body is treated the same way and never thrown.
  const res = await fetch(`${BASE}/api/me`, { credentials: 'include' })
  if (!res.ok) return null
  const data = (await res.json().catch(() => null)) as MeResponse | null
  if (!data?.display_name) return null
  return { displayName: data.display_name }
}

async function beginLogin(): Promise<void> {
  if (FAKE_LOGIN) {
    await fetch(`${BASE}/api/auth/fake-login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona: 'leader' }),
    })
    return
  }
  window.location.href = `${BASE}/api/auth/begin`
}

async function logout(): Promise<void> {
  await fetch(`${BASE}/api/auth/logout`, { method: 'POST', credentials: 'include' })
}

async function getGroups(): Promise<GroupDetail[]> {
  const res = await fetch(`${BASE}/api/me/groups`, { credentials: 'include' })
  if (!res.ok) throw new Error(`Could not load your groups (${res.status}).`)
  const data = (await res.json()) as GroupResponse[]
  return data.map((g) => ({
    id: g.id,
    name: g.name,
    bodyType: g.body_type,
    members: g.members.map(toRosterMember),
  }))
}

async function searchMember(email: string): Promise<SearchOutcome> {
  const res = await fetch(`${BASE}/api/members/search`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!res.ok) throw new Error(`Search failed (${res.status}).`)
  const data = (await res.json()) as SearchResponse
  if (data.status === 'good_standing') {
    return {
      status: 'good_standing',
      member: { id: data.member.id, name: data.member.full_name, email: data.member.email },
    }
  }
  return { status: data.status }
}

async function addMember(groupId: string, memberId: string): Promise<RosterMember> {
  const res = await fetch(`${BASE}/api/groups/${groupId}/members`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_id: memberId }),
  })
  if (!res.ok) throw new Error(`The change failed (${res.status}).`)
  const data = (await res.json()) as GroupMemberResponse
  return toRosterMember(data)
}

async function removeMember(groupId: string, memberId: string): Promise<void> {
  const res = await fetch(`${BASE}/api/groups/${groupId}/members/${memberId}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`The change failed (${res.status}).`)
}

export const httpApi: Api = {
  getSession,
  beginLogin,
  logout,
  getGroups,
  searchMember,
  addMember,
  removeMember,
}
