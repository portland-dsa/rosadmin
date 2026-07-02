import { Tombstone } from './Tombstone'

type MemberCardProps = {
  name: string
  email: string
  leader?: boolean
  you?: boolean
  justAdded?: boolean
  removing?: boolean
  onRemove?: () => void
  onUndo?: () => void
}

export function MemberCard({
  name,
  email,
  leader,
  you,
  justAdded,
  removing,
  onRemove,
  onUndo,
}: MemberCardProps) {
  const className = [
    'member-card',
    you && 'member-card--you',
    justAdded && 'member-card--added',
    removing && 'member-card--removing',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className} role="listitem">
      <span
        className={
          leader
            ? 'member-card__mark member-card__mark--leader'
            : 'member-card__mark member-card__mark--member'
        }
        aria-hidden="true"
      >
        {leader ? '★' : '☆'}
      </span>
      <div className="member-card__body">
        <div className="member-card__name">
          {name}
          {you && <span className="member-card__you">YOU</span>}
          {justAdded && <span className="member-card__added">Added</span>}
        </div>
        <div className="member-card__email">{email}</div>
      </div>
      <div className="member-card__action">
        {/* Leaders can't be removed; they show a lock instead of a control. */}
        {leader ? (
          <span className="member-card__role">
            <span className="member-card__lock" aria-hidden="true">
              🔒
            </span>{' '}
            Leader
          </span>
        ) : (
          onRemove && (
            <button type="button" className="member-card__remove" onClick={onRemove}>
              Remove
            </button>
          )
        )}
      </div>
      {removing && <Tombstone onUndo={onUndo} />}
    </div>
  )
}
