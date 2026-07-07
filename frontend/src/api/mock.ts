import type { Api } from './contract'
import type { GroupDetail, Role, RosterMember, SearchOutcome, Session } from '../types'

/* The whole-organization directory the add-by-email search looks through. Ids are
   opaque UUIDs and email is a separate field, matching the real backend. The names
   are deliberately placeholder-shaped so no one mistakes them for real members. */
type Person = { id: string; name: string; email: string }

const directory: Person[] = [
  { id: '92e39c67-0854-4f5e-bc0e-5f66a603d55b', name: 'Test NM', email: 'a@example.com' },
  { id: '3c50ff78-8b5f-47e1-9e87-32c92519fae9', name: 'Test C', email: 'b@example.com' },
  { id: '6d35b6cb-d311-4bc0-9f74-122ec5617c81', name: 'Test S', email: 'c@example.com' },
  { id: '75f9e6a6-094d-4aee-b12e-ba9b81f13e63', name: 'Test W', email: 'd@example.com' },
  { id: '9040e862-0767-4190-b6b1-a3b34b0dd132', name: 'Test D', email: 'e@example.com' },
  { id: '3929ec38-ccd5-475f-b0b0-51d3ec0c3476', name: 'Test', email: 'f@example.com' },
  { id: '7c5aeac1-5c80-49dd-bf27-0ab2de93f419', name: 'Test G', email: 'g@example.com' },
  { id: 'df4d9d06-3a28-43e0-bd3d-2c9cee8ac118', name: 'Test I', email: 'h@example.com' },
  { id: 'e301be8c-beff-487c-a376-75038c45b468', name: 'Test P', email: 'i@example.com' },
  { id: '630893cc-7c38-42b9-a84f-f4d2da4a3e26', name: 'Test P', email: 'j@example.com' },
  { id: 'ab546272-d606-4613-85ea-2d53f0be6acb', name: 'Test S', email: 'k@example.com' },
  { id: '41067af6-ca2d-4c9a-8d7f-ae7f625abd03', name: 'Test Z', email: 'l@example.com' },
  { id: '2dab965f-1dc5-45d7-b04d-00d8de350e86', name: 'Test T', email: 'm@example.com' },
  { id: '113946b6-15f7-4384-ad57-58ee2daf19d9', name: 'Test V', email: 'n@example.com' },
  { id: '21a19a23-715f-4d50-84be-bc0e2151765a', name: 'Test P', email: 'o@example.com' },
  {
    id: '59984645-f8ef-41d1-921b-b95144e1e757',
    name: 'Whatanextremelylongname Someone',
    email: 'longname@example.com',
  },
]

/* Not in the directory: searching this address exercises the dues-lapsed miss. */
const LAPSED_EMAIL = 'lapsed@example.com'

function person(id: string): Person {
  const p = directory.find((d) => d.id === id)
  if (!p) throw new Error(`mock: unknown member ${id}`)
  return p
}

/* Group membership as ids + roles; names and emails resolve from the directory. */
type GroupSeed = {
  id: string
  name: string
  bodyType: string
  members: { id: string; role: Role }[]
}

const groups: Record<string, GroupSeed> = {
  communications: {
    id: 'communications',
    name: 'Communications Committee',
    bodyType: 'Committee',
    members: [
      { id: '92e39c67-0854-4f5e-bc0e-5f66a603d55b', role: 'leader' },
      { id: '3c50ff78-8b5f-47e1-9e87-32c92519fae9', role: 'leader' },
      { id: '9040e862-0767-4190-b6b1-a3b34b0dd132', role: 'member' },
      { id: '3929ec38-ccd5-475f-b0b0-51d3ec0c3476', role: 'member' },
      { id: '7c5aeac1-5c80-49dd-bf27-0ab2de93f419', role: 'member' },
      { id: '59984645-f8ef-41d1-921b-b95144e1e757', role: 'member' },
      { id: 'df4d9d06-3a28-43e0-bd3d-2c9cee8ac118', role: 'member' },
      { id: 'e301be8c-beff-487c-a376-75038c45b468', role: 'member' },
      { id: '630893cc-7c38-42b9-a84f-f4d2da4a3e26', role: 'member' },
      { id: 'ab546272-d606-4613-85ea-2d53f0be6acb', role: 'member' },
      { id: '41067af6-ca2d-4c9a-8d7f-ae7f625abd03', role: 'member' },
    ],
  },
  electoral: {
    id: 'electoral',
    name: 'Electoral Working Group',
    bodyType: 'Working Group',
    members: [
      { id: '92e39c67-0854-4f5e-bc0e-5f66a603d55b', role: 'leader' },
      { id: '2dab965f-1dc5-45d7-b04d-00d8de350e86', role: 'member' },
      { id: '113946b6-15f7-4384-ad57-58ee2daf19d9', role: 'member' },
    ],
  },
}

/* The mock's signed-in leader: a leader of the groups above, so the "you" badge
   and the leader-lock paths are exercised. */
const SESSION: Session = { displayName: 'Test NM' }

let loggedIn = false

/* A little latency so the UI's loading paths are real, not instant. */
const MOCK_DELAY = 200
function delay<T>(value: T, ms = MOCK_DELAY): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

function rosterOf(seed: GroupSeed): RosterMember[] {
  return seed.members.map((m) => {
    const p = person(m.id)
    return { id: p.id, name: p.name, email: p.email, role: m.role }
  })
}

async function getSession(): Promise<Session | null> {
  return delay(loggedIn ? SESSION : null)
}

async function beginLogin(): Promise<void> {
  loggedIn = true
  await delay(null)
}

async function logout(): Promise<void> {
  loggedIn = false
  await delay(null)
}

async function getGroups(): Promise<GroupDetail[]> {
  return delay(
    Object.values(groups).map((seed) => ({
      id: seed.id,
      name: seed.name,
      bodyType: seed.bodyType,
      members: rosterOf(seed),
    })),
  )
}

async function searchMember(email: string): Promise<SearchOutcome> {
  const q = email.trim().toLowerCase()
  const hit = directory.find((p) => p.email.toLowerCase() === q)
  if (hit) {
    return delay({ status: 'good_standing', member: { id: hit.id, name: hit.name, email: hit.email } })
  }
  if (q === LAPSED_EMAIL) return delay({ status: 'dues_expired' })
  return delay({ status: 'not_found' })
}

async function addMember(groupId: string, memberId: string): Promise<RosterMember> {
  const seed = groups[groupId]
  if (!seed) throw new Error('The change failed.')
  const p = person(memberId)
  seed.members = [...seed.members.filter((m) => m.id !== memberId), { id: memberId, role: 'member' }]
  return delay({ id: p.id, name: p.name, email: p.email, role: 'member' })
}

async function removeMember(groupId: string, memberId: string): Promise<void> {
  const seed = groups[groupId]
  if (!seed) throw new Error('The change failed.')
  seed.members = seed.members.filter((m) => m.id !== memberId)
  await delay(null)
}

export const mockApi: Api = {
  getSession,
  beginLogin,
  logout,
  getGroups,
  searchMember,
  addMember,
  removeMember,
}
