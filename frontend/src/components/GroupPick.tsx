import type { Group } from '../types'

type GroupPickProps = {
  groups: Group[]
  selectedId: string | null
  onSelect: (id: string) => void
}

/* The group name rendered as a dropdown: leaders of more than one group click
   the name to switch. With a single group it's just the name, no control. The
   name comes from the bodies list, so it's stable across a roster reload. */
export function GroupPick({ groups, selectedId, onSelect }: GroupPickProps) {
  const multi = groups.length > 1
  const selectedName = groups.find((g) => g.id === selectedId)?.name

  return (
    <div className={multi ? 'grouppick' : 'grouppick grouppick--single'}>
      <h1 className="grouppick__name">{selectedName || '—'}</h1>
      {multi && (
        <>
          <span className="grouppick__caret" aria-hidden="true">
            ▾
          </span>
          <select
            className="grouppick__select"
            aria-label="Switch group"
            value={selectedId ?? ''}
            onChange={(e) => onSelect(e.target.value)}
          >
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  )
}
