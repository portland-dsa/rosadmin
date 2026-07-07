import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api } from './api'
import type { Group, GroupDetail, Member, RosterMember, Session } from './types'

export type Mode = 'filter' | 'add'

/* The undo window for a deferred removal. Must match the .tombstone__bar
   countdown animation duration in styles.css. */
const UNDO_MS = 6000

/* How long a transient add-slot message (not found, already a member, added)
   lingers before reverting to the hint. Must match the .addslot__bar
   countdown animation duration in styles.css. */
const MSG_MS = 4500

/* The add panel's state through one search-and-confirm cycle. */
export type AddPanelState =
  | { kind: 'idle' }
  | { kind: 'searching' }
  | { kind: 'found'; member: Member }
  | { kind: 'notfound'; email: string }
  | { kind: 'alreadymember'; name: string; groupName: string }
  | { kind: 'added'; name: string; groupName: string }
  | { kind: 'error'; message: string }

/* The interlinked group state — roster, the add panel, the recently-added
   list, and in-flight removals all move together, so they live in one
   reducer. */
type GroupState = {
  loading: boolean
  error: string | null
  group: GroupDetail | null
  add: AddPanelState
  recentlyAdded: RosterMember[]
  removingIds: string[]
}

type Action =
  | { type: 'reset' }
  | { type: 'loadStart' }
  | { type: 'loadOk'; group: GroupDetail }
  | { type: 'loadErr'; message: string }
  | { type: 'searchStart' }
  | { type: 'searchFound'; member: Member }
  | { type: 'searchNotFound'; email: string }
  | { type: 'searchAlreadyMember'; name: string; groupName: string }
  | { type: 'searchErr'; message: string }
  | { type: 'clearAdd' }
  | { type: 'added'; member: RosterMember; groupName: string }
  | { type: 'addErr'; message: string }
  | { type: 'removeStart'; id: string }
  | { type: 'removeUndo'; id: string }
  | { type: 'removeCommit'; id: string }
  | { type: 'removeErr'; id: string }

const initialState: GroupState = {
  loading: false,
  error: null,
  group: null,
  add: { kind: 'idle' },
  recentlyAdded: [],
  removingIds: [],
}

function reducer(state: GroupState, action: Action): GroupState {
  switch (action.type) {
    case 'reset':
      return initialState
    case 'loadStart':
      return { ...initialState, loading: true }
    case 'loadOk':
      return { ...initialState, group: action.group }
    case 'loadErr':
      return { ...initialState, error: action.message }

    case 'searchStart':
      return { ...state, add: { kind: 'searching' } }
    case 'searchFound':
      return { ...state, add: { kind: 'found', member: action.member } }
    case 'searchNotFound':
      return { ...state, add: { kind: 'notfound', email: action.email } }
    case 'searchAlreadyMember':
      return { ...state, add: { kind: 'alreadymember', name: action.name, groupName: action.groupName } }
    case 'searchErr':
      return { ...state, add: { kind: 'error', message: action.message } }
    case 'clearAdd':
      return { ...state, add: { kind: 'idle' } }

    case 'added': {
      if (!state.group) return state
      // The new member rides at the very top of the roster, where the green
      // highlight and "Added" chip make them easy to spot.
      const roster = [
        action.member,
        ...state.group.members.filter((m) => m.id !== action.member.id),
      ]
      return {
        ...state,
        group: { ...state.group, members: roster },
        recentlyAdded: [
          action.member,
          ...state.recentlyAdded.filter((m) => m.id !== action.member.id),
        ],
        add: { kind: 'added', name: action.member.name, groupName: action.groupName },
      }
    }
    case 'addErr':
      return { ...state, add: { kind: 'error', message: action.message } }

    case 'removeStart':
      return { ...state, removingIds: [...state.removingIds, action.id] }
    case 'removeUndo':
      return { ...state, removingIds: state.removingIds.filter((id) => id !== action.id) }
    case 'removeCommit': {
      if (!state.group) return state
      return {
        ...state,
        group: {
          ...state.group,
          members: state.group.members.filter((m) => m.id !== action.id),
        },
        recentlyAdded: state.recentlyAdded.filter((m) => m.id !== action.id),
        removingIds: state.removingIds.filter((id) => id !== action.id),
      }
    }
    case 'removeErr':
      // The request failed, so the member stays; just drop the tombstone.
      return { ...state, removingIds: state.removingIds.filter((id) => id !== action.id) }

    default:
      return state
  }
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : 'Something went wrong.'
}

function matchesQuery(m: RosterMember, query: string): boolean {
  const needle = query.trim().toLowerCase()
  return m.name.toLowerCase().includes(needle) || m.email.toLowerCase().includes(needle)
}

export function useGroupAdmin() {
  const [session, setSession] = useState<Session | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [groups, setGroups] = useState<Group[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('filter')
  const [query, setQuery] = useState('')
  const [state, dispatch] = useReducer(reducer, initialState)

  /* setTimeout handles for deferred removals, keyed by member id. */
  const removeTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  // Check for an existing session on first load.
  useEffect(() => {
    let active = true
    api
      .getSession()
      .then((s) => {
        if (!active) return
        setSession(s)
        setSessionLoading(false)
      })
      .catch((e) => {
        // A rejected fetch here is usually a network/CORS/cookie fault rather
        // than a logged-out user — log it so it stays visible while wiring up
        // Discord login, but still fall through to the login screen.
        console.warn('getSession failed', e)
        if (active) setSessionLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  // Once signed in, load the groups this leader administers.
  useEffect(() => {
    if (!session) return
    let active = true
    api
      .getBodies()
      .then((gs) => {
        if (!active) return
        setGroups(gs)
        setSelectedId((cur) => cur ?? gs[0]?.id ?? null)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [session])

  // Load the roster whenever the selected group changes.
  useEffect(() => {
    if (!selectedId) return
    let active = true
    dispatch({ type: 'loadStart' })
    api
      .getBody(selectedId)
      .then((g) => {
        if (active) dispatch({ type: 'loadOk', group: g })
      })
      .catch((e) => {
        if (active) dispatch({ type: 'loadErr', message: errText(e) })
      })
    return () => {
      active = false
    }
  }, [selectedId])

  // Cancel any pending removal timers on unmount.
  useEffect(() => {
    const timers = removeTimers.current
    return () => {
      timers.forEach((t) => clearTimeout(t))
    }
  }, [])

  // A transient add-slot message (not found / already a member / added / error)
  // reverts to the hint after its countdown, leaving the column at rest. A
  // found match waits for the leader to confirm or dismiss, so it never times out.
  useEffect(() => {
    const transient = ['notfound', 'alreadymember', 'added', 'error']
    if (!transient.includes(state.add.kind)) return
    const t = setTimeout(() => dispatch({ type: 'clearAdd' }), MSG_MS)
    return () => clearTimeout(t)
  }, [state.add])

  const login = useCallback(async () => {
    api.beginLogin()
    // In the real client beginLogin navigates away; in the mock it flips a
    // flag, so re-reading the session here picks the new login up.
    const s = await api.getSession()
    setSession(s)
  }, [])

  const logout = useCallback(async () => {
    removeTimers.current.forEach((t) => clearTimeout(t))
    removeTimers.current.clear()
    await api.logout()
    setSession(null)
    setGroups([])
    setSelectedId(null)
    setMode('filter')
    setQuery('')
    dispatch({ type: 'reset' })
  }, [])

  const changeMode = useCallback((next: Mode) => {
    setMode(next)
    setQuery('')
    if (next === 'filter') dispatch({ type: 'clearAdd' })
  }, [])

  const selectGroup = useCallback((id: string) => {
    setSelectedId(id)
    setMode('filter')
    setQuery('')
  }, [])

  const search = useCallback(async () => {
    const email = query.trim()
    if (!email) return
    dispatch({ type: 'searchStart' })
    try {
      const res = await api.searchMembers(email)
      const match = res.matches[0]
      if (!match) {
        dispatch({ type: 'searchNotFound', email })
      } else if (state.group?.members.some((m) => m.id === match.id)) {
        dispatch({ type: 'searchAlreadyMember', name: match.name, groupName: state.group.name })
      } else {
        dispatch({ type: 'searchFound', member: match })
      }
    } catch (e) {
      dispatch({ type: 'searchErr', message: errText(e) })
    }
  }, [query, state.group])

  const clearAdd = useCallback(() => dispatch({ type: 'clearAdd' }), [])

  const confirmAdd = useCallback(
    async (member: Member) => {
      if (!selectedId || !state.group) return
      if (state.group.members.some((m) => m.id === member.id)) return
      const groupName = state.group.name
      try {
        await api.updateMemberGroups(member.id, [
          { id: selectedId, role: 'member', remove: false },
        ])
        dispatch({ type: 'added', member: { ...member, role: 'member' }, groupName })
        setQuery('')
      } catch (e) {
        dispatch({ type: 'addErr', message: errText(e) })
      }
    },
    [selectedId, state.group],
  )

  // Removal is deferred: show a tombstone, and only fire the request after the
  // undo window elapses. Undo cancels the timer before anything is sent.
  const startRemove = useCallback(
    (member: RosterMember) => {
      if (!selectedId) return
      dispatch({ type: 'removeStart', id: member.id })
      const timer = setTimeout(() => {
        removeTimers.current.delete(member.id)
        api
          .updateMemberGroups(member.id, [{ id: selectedId, role: member.role, remove: true }])
          .then(() => dispatch({ type: 'removeCommit', id: member.id }))
          .catch(() => dispatch({ type: 'removeErr', id: member.id }))
      }, UNDO_MS)
      removeTimers.current.set(member.id, timer)
    },
    [selectedId],
  )

  const undoRemove = useCallback((id: string) => {
    const timer = removeTimers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      removeTimers.current.delete(id)
    }
    dispatch({ type: 'removeUndo', id })
  }, [])

  const roster = state.group?.members ?? []
  const visibleMembers =
    mode === 'filter' && query.trim() ? roster.filter((m) => matchesQuery(m, query)) : roster
  const counts = {
    people: roster.length,
    leaders: roster.filter((m) => m.role === 'leader').length,
  }

  return {
    session,
    sessionLoading,
    login,
    logout,
    groups,
    selectedId,
    selectGroup,
    mode,
    setMode: changeMode,
    query,
    setQuery,
    loading: state.loading,
    error: state.error,
    group: state.group,
    visibleMembers,
    counts,
    add: state.add,
    search,
    clearAdd,
    confirmAdd,
    recentlyAdded: state.recentlyAdded,
    removingIds: state.removingIds,
    startRemove,
    undoRemove,
  }
}
