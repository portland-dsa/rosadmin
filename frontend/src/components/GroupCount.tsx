type GroupCountProps = {
  people: number
  leaders: number
}

/* "20 people · 2 leaders" — the people total is emphasized; shown in both the
   mobile and desktop title copies. */
export function GroupCount({ people, leaders }: GroupCountProps) {
  return (
    <>
      <b>
        {people} {people === 1 ? 'person' : 'people'}
      </b>{' '}
      &middot; {leaders} {leaders === 1 ? 'leader' : 'leaders'}
    </>
  )
}
