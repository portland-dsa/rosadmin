import type { RosterMember } from '../types'
import { MemberCard } from './MemberCard'

type MemberGridProps = {
  members: RosterMember[]
  removingIds: string[]
  justAddedIds: Set<string>
  selfId: string
  groupName: string
  filterTerm: string
  onStartRemove: (member: RosterMember) => void
  onUndoRemove: (id: string) => void
}

export function MemberGrid({
  members,
  removingIds,
  justAddedIds,
  selfId,
  groupName,
  filterTerm,
  onStartRemove,
  onUndoRemove,
}: MemberGridProps) {
  if (members.length === 0) {
    return (
      <div className="empty">
        {filterTerm ? (
          <p>
            No one in {groupName} matches &ldquo;{filterTerm}&rdquo;.
          </p>
        ) : (
          <p>No members to show.</p>
        )}
      </div>
    )
  }

  return (
    <div className="member-grid" role="list">
      {members.map((m) => {
        const removing = removingIds.includes(m.id)
        return (
          <MemberCard
            key={m.id}
            name={m.name}
            email={m.email}
            leader={m.role === 'leader'}
            you={m.id === selfId}
            justAdded={justAddedIds.has(m.id)}
            onRemove={removing ? undefined : () => onStartRemove(m)}
            removing={removing}
            onUndo={() => onUndoRemove(m.id)}
          />
        )
      })}
    </div>
  )
}
