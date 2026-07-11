type LoginScreenProps = {
  onLogin: () => void
  // The last login round-trip verified the Discord member but the chapter-leader
  // gate refused them; show a quiet notice under the card.
  denied?: boolean
}

export function LoginScreen({ onLogin, denied }: LoginScreenProps) {
  return (
    <main className="login">
      <div className="login__card">
        <span className="login__star" aria-hidden="true">★</span>
        <h1 className="login__wordmark">Portland DSA</h1>
        <p className="login__blurb">Group membership administration</p>
        <button type="button" className="login__button" onClick={onLogin}>
          Sign in with Discord
        </button>
      </div>
      {denied && (
        <p className="login__unauth_notice" role="alert">
          Unauthorized
        </p>
      )}
    </main>
  )
}
