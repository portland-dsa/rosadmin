import type { ReactNode } from 'react'
import type { Member } from '../types'
import type { AddPanelState } from '../useGroupAdmin'

type AddPanelProps = {
  add: AddPanelState
  onConfirm: (member: Member) => void
}

type AddPanelMessage = { good: boolean; text: ReactNode }

/* Maps a transient add state to the message shown in the slot. A found match
   and the idle/searching states render their own way, so they return null. */
function addPanelMessage(add: AddPanelState): AddPanelMessage | null {
  switch (add.kind) {
    case 'notfound':
      return {
        good: false,
        text: (
          <>
            No one in Portland DSA has the email <b>{add.email}</b>. Double-check the spelling.
          </>
        ),
      }
    case 'alreadymember':
      return {
        good: false,
        text: (
          <>
            <b>{add.name}</b> is already in {add.groupName}.
          </>
        ),
      }
    case 'added':
      return {
        good: true,
        text: (
          <>
            Added <b>{add.name}</b> to {add.groupName} &mdash; it&rsquo;s at the top of the list.
          </>
        ),
      }
    case 'error':
      return { good: false, text: add.message }
    default:
      return null
  }
}

/* The reserved column beside the search bar. A found match becomes a confirm
   card; not-found / already-a-member / added become self-reverting messages;
   otherwise a standing hint keeps the column from reading as empty. */
export function AddPanel({ add, onConfirm }: AddPanelProps) {
  if (add.kind === 'found') {
    const m = add.member
    return (
      <div className="addslot">
        <div className="addslot__hit">
          <div className="addslot__hittop">
            <span className="addslot__mark" aria-hidden="true">
              ☆
            </span>
            <span className="addslot__name">{m.name}</span>
            <button type="button" className="addslot__add" onClick={() => onConfirm(m)}>
              Add
            </button>
          </div>
          <div className="addslot__email">{m.email}</div>
        </div>
      </div>
    )
  }

  const message = addPanelMessage(add)
  if (message) {
    return (
      <div className="addslot">
        <div className={message.good ? 'addslot__msg addslot__msg--good' : 'addslot__msg'}>
          <span className="addslot__msgicon" aria-hidden="true">
            {message.good ? '✓' : '⚠'}
          </span>
          <span className="addslot__msgtext">{message.text}</span>
          <span className="addslot__bar" />
        </div>
      </div>
    )
  }

  return (
    <div className="addslot">
      <div className="addslot__placeholder">
        <span>
          Switch to <b>➕ Add</b> (to the left) and enter an email — your match appears here to
          confirm.
        </span>
      </div>
    </div>
  )
}
