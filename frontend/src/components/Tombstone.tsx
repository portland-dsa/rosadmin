type TombstoneProps = {
  onUndo?: () => void
}

/* The overlay shown while a removal counts down. Undo cancels before the
   request is sent; the bar animates the time left. */
export function Tombstone({ onUndo }: TombstoneProps) {
  return (
    <div className="tombstone">
      <span className="tombstone__label">Removing...</span>
      <button type="button" className="tombstone__undo" onClick={onUndo}>
        Undo
      </button>
      <span className="tombstone__bar" />
    </div>
  )
}
