type LoginScreenProps = {
  onLogin: () => void
}

export function LoginScreen({ onLogin }: LoginScreenProps) {
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
    </main>
  )
}
