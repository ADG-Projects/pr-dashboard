import { useState, type FormEvent } from 'react';
import styles from './Login.module.css';

interface Props {
  onLogin: () => void;
  oauthError?: string | null;
}

export function Login({ onLogin, oauthError }: Props) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        setError('Invalid password');
        return;
      }
      const data = await res.json();
      if (data.authenticated) {
        onLogin();
      } else {
        setError('Invalid password');
      }
    } catch {
      setError('Connection failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.container}>
      <form className={styles.card} onSubmit={handleSubmit}>
        <h1 className={styles.title}>PR Dashboard</h1>
        <p className={styles.subtitle}>Enter password to continue</p>
        {(error || oauthError) && <div className={styles.error}>{error || oauthError}</div>}
        <input
          className={styles.input}
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        <button className={styles.button} type="submit" disabled={loading}>
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
