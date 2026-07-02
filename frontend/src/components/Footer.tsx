type FooterProps = {
  onLogout: () => void
}

export function Footer({ onLogout }: FooterProps) {
  return (
    <footer className="footer">
      <div className="footer__inner">
        <a className="footer__link" href="#">Help</a>
        <a className="footer__link" href="#">Portland DSA</a>
        <button type="button" className="footer__logout" onClick={onLogout}>
          Log out
        </button>
      </div>
    </footer>
  )
}
