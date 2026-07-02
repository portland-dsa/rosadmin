type PageHeaderProps = {
  memberName: string
}

export function PageHeader({ memberName }: PageHeaderProps) {
  return (
    <header className="pageheader">
      <div className="pageheader__inner">
        <span className="pageheader__star" aria-hidden="true">★</span>
        <span className="pageheader__wordmark">Portland DSA</span>
        <span className="pageheader__signin">
          Signed in as <b>{memberName}</b>
        </span>
      </div>
    </header>
  )
}
