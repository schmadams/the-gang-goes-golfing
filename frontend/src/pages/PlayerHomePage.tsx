import { useAuth } from "../auth/AuthProvider";

export function PlayerHomePage() {
  const { user, signOut } = useAuth();

  return (
    <main style={{ padding: 24 }}>
      <header style={{ display: "flex", justifyContent: "space-between" }}>
        <div>
          <h1>Player homepage</h1>
          <p>Signed in as {user?.email}</p>
        </div>

        <button type="button" onClick={signOut}>
          Sign out
        </button>
      </header>

      <section>
        <h2>Your groups</h2>
        <p>This page will list the groups linked to this signed-in player.</p>
      </section>
    </main>
  );
}