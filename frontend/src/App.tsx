import { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider, MutationCache } from '@tanstack/react-query';
import { Shell } from './components/Shell';
import { OrgOverview } from './pages/OrgOverview';
import { PrioritizeView } from './pages/PrioritizeView';
import { RepoView } from './pages/RepoView';
import { Login } from './pages/Login';
import type { GitHubUser } from './api/client';

// Global toast state — shared via context
let _showToast: (msg: string) => void = () => {};

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
  mutationCache: new MutationCache({
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Something went wrong';
      _showToast(message);
    },
  }),
});

export interface BannerInfo {
  message: string;
  type: 'info' | 'error';
}

interface UserContextValue {
  user: GitHubUser | null;
  setUser: (u: GitHubUser | null) => void;
  oauthConfigured: boolean;
  banner: BannerInfo | null;
  setBanner: (b: BannerInfo | null) => void;
}

export const UserContext = createContext<UserContextValue>({
  user: null,
  setUser: () => {},
  oauthConfigured: false,
  banner: null,
  setBanner: () => {},
});

export function useCurrentUser() {
  return useContext(UserContext);
}

export default function App() {
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(true);
  const [user, setUser] = useState<GitHubUser | null>(null);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [banner, setBanner] = useState<BannerInfo | null>(null);
  const [oauthError, setOauthError] = useState<string | null>(null);

  const [toast, setToast] = useState<string | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 5000);
  }, []);

  // Wire up global toast function
  useEffect(() => { _showToast = showToast; }, [showToast]);

  useEffect(() => {
    fetch('/api/auth/me', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        setAuthenticated(data.authenticated);
        setAuthEnabled(data.auth_enabled);
        setOauthConfigured(data.oauth_configured ?? false);
        if (data.user) setUser(data.user);
        setAuthChecked(true);

        // Handle OAuth redirect params
        const params = new URLSearchParams(window.location.search);
        const errorCode = params.get('error');
        if (errorCode) {
          const messages: Record<string, string> = {
            invalid_state: 'Sign-in failed: invalid or expired link. Please try again.',
            state_expired: 'Sign-in link expired. Please try again.',
            token_exchange_failed: 'GitHub is temporarily unavailable. Please try again in a moment.',
            no_token: 'GitHub did not return an access token. Please try again.',
            user_fetch_failed: 'Could not fetch your GitHub profile. Please try again.',
            user_not_found: 'Your session has expired. Please sign in again.',
          };
          const msg = messages[errorCode] || `Sign-in failed: ${errorCode.replace(/_/g, ' ')}`;
          // Set both: oauthError for Login page, banner for Shell (if already authenticated)
          setOauthError(msg);
          setBanner({ message: msg, type: 'error' });
          window.history.replaceState({}, '', window.location.pathname);
        } else if (params.get('linked_existing') === 'true' && data.user) {
          setBanner({
            message: `This GitHub identity was already linked to your account. Signed in as ${data.user.name || data.user.login}.`,
            type: 'info',
          });
          window.history.replaceState({}, '', window.location.pathname);
          setTimeout(() => setBanner(null), 8000);
        }
      })
      .catch(() => setAuthChecked(true));
  }, []);

  if (!authChecked) return null;

  async function handleLogin() {
    const resp = await fetch('/api/auth/me', { credentials: 'include' });
    const data = await resp.json();
    setAuthenticated(data.authenticated);
    setOauthConfigured(data.oauth_configured ?? false);
    if (data.user) setUser(data.user);
  }

  if (authEnabled && !authenticated) {
    return <Login onLogin={handleLogin} oauthError={oauthError} />;
  }

  return (
    <UserContext.Provider value={{ user, setUser, oauthConfigured, banner, setBanner }}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<Shell />}>
              <Route path="/" element={<OrgOverview />} />
              <Route path="/prioritize" element={<PrioritizeView />} />
              <Route path="/repos/:owner/:name" element={<RepoView />} />
              <Route path="/repos/:owner/:name/stacks/:stackId" element={<StackRedirect />} />
            </Route>
          </Routes>
        </BrowserRouter>
        {toast && (
          <div
            style={{
              position: 'fixed', bottom: 20, right: 20, zIndex: 9999,
              background: 'var(--ci-fail, #d73a4a)', color: '#fff',
              padding: '10px 16px', borderRadius: 8, fontSize: '0.9rem',
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)', cursor: 'pointer',
              maxWidth: 400,
            }}
            onClick={() => setToast(null)}
          >
            {toast}
          </div>
        )}
      </QueryClientProvider>
    </UserContext.Provider>
  );
}

function StackRedirect() {
  const params = window.location.pathname.match(/\/repos\/([^/]+)\/([^/]+)/);
  if (params) {
    return <Navigate to={`/repos/${params[1]}/${params[2]}`} replace />;
  }
  return <Navigate to="/" replace />;
}
