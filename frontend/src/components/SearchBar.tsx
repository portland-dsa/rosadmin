import type { FormEvent } from 'react'
import type { Group } from '../types'
import type { Mode } from '../useGroupAdmin'
import { GroupPick } from './GroupPick'
import { GroupCount } from './GroupCount'

type SearchBarProps = {
  mode: Mode
  query: string
  onModeChange: (mode: Mode) => void
  onQueryChange: (query: string) => void
  onSearch: () => void
  groups: Group[]
  selectedId: string | null
  onSelect: (id: string) => void
  people: number
  leaders: number
}

const PLACEHOLDER: Record<Mode, string> = {
  filter: 'Filter this group by name or email',
  add: 'Search an email to add',
}

export function SearchBar({
  mode,
  query,
  onModeChange,
  onQueryChange,
  onSearch,
  groups,
  selectedId,
  onSelect,
  people,
  leaders,
}: SearchBarProps) {
  const isAdd = mode === 'add'

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    // Filtering is live as you type; only Add needs the submit to fire a search.
    if (isAdd) onSearch()
  }

  return (
    <div className={isAdd ? 'searchbar searchbar--add' : 'searchbar'}>
      <div className="searchbar__tabs" role="group" aria-label="Choose what the box does">
        <button
          type="button"
          className={isAdd ? 'searchbar__tab' : 'searchbar__tab is-active'}
          aria-pressed={!isAdd}
          onClick={() => onModeChange('filter')}
        >
          <span aria-hidden="true">🔍</span> Filter
        </button>
        <button
          type="button"
          className={
            isAdd ? 'searchbar__tab searchbar__tab--add is-active' : 'searchbar__tab searchbar__tab--add'
          }
          aria-pressed={isAdd}
          onClick={() => onModeChange('add')}
        >
          <span aria-hidden="true">➕</span> Add
        </button>
      </div>

      {/* Desktop title, tucked beside the tabs; CSS hides it on mobile, where
          the toolbar shows its own copy above the bar. */}
      <div className="searchbar__title">
        <span className="searchbar__eyebrow">You lead</span>
        <GroupPick groups={groups} selectedId={selectedId} onSelect={onSelect} />
        <p className="searchbar__count">
          <GroupCount people={people} leaders={leaders} />
        </p>
      </div>

      <form className="searchbar__form" role="search" onSubmit={handleSubmit}>
        <input
          className="searchbar__input"
          type="search"
          autoComplete="off"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={PLACEHOLDER[mode]}
          aria-label={PLACEHOLDER[mode]}
        />
        <button type="submit" className="searchbar__go">
          {isAdd ? 'Add' : 'Filter'}
        </button>
      </form>
    </div>
  )
}
