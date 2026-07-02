import type { Group, Member } from '../types'
import type { AddPanelState, Mode } from '../useGroupAdmin'
import { SearchBar } from './SearchBar'
import { AddPanel } from './AddPanel'
import { GroupPick } from './GroupPick'
import { GroupCount } from './GroupCount'

type ControlPanelProps = {
  groups: Group[]
  selectedId: string | null
  onSelect: (id: string) => void
  people: number
  leaders: number
  mode: Mode
  query: string
  onModeChange: (mode: Mode) => void
  onQueryChange: (query: string) => void
  onSearch: () => void
  add: AddPanelState
  onConfirmAdd: (member: Member) => void
}

/* The sticky header: tabs, the group title, the search row, and the add slot.
   The group title appears twice — beside the tabs on desktop (inside SearchBar) and
   above the bar on mobile — with CSS choosing which copy shows. */
export function ControlPanel({
  groups,
  selectedId,
  onSelect,
  people,
  leaders,
  mode,
  query,
  onModeChange,
  onQueryChange,
  onSearch,
  add,
  onConfirmAdd,
}: ControlPanelProps) {
  return (
    <div className="toolbar">
      <div className="toolbar__titlemobile">
        <div>
          <div className="toolbar__eyebrow">You lead</div>
          <GroupPick groups={groups} selectedId={selectedId} onSelect={onSelect} />
        </div>
        <div className="toolbar__count">
          <GroupCount people={people} leaders={leaders} />
        </div>
      </div>

      <div className="toolbar__row">
        <SearchBar
          mode={mode}
          query={query}
          onModeChange={onModeChange}
          onQueryChange={onQueryChange}
          onSearch={onSearch}
          groups={groups}
          selectedId={selectedId}
          onSelect={onSelect}
          people={people}
          leaders={leaders}
        />
        <AddPanel add={add} onConfirm={onConfirmAdd} />
      </div>
    </div>
  )
}
