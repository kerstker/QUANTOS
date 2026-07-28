import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';

export interface LocationSnapshot {
  pathname: string;
  search: string;
  hash: string;
}

interface NavigateOptions {
  replace?: boolean;
}

interface RouterContextValue {
  location: LocationSnapshot;
  navigate: (to: string, options?: NavigateOptions) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);
const ParamsContext = createContext<Record<string, string>>({});

function parseLocation(value: string): LocationSnapshot {
  const url = new URL(value, 'http://quantos.local');
  return { pathname: url.pathname || '/', search: url.search, hash: url.hash };
}

function useRouter(): RouterContextValue {
  const context = useContext(RouterContext);
  if (!context) throw new Error('router hook used outside BrowserRouter or MemoryRouter');
  return context;
}

export function BrowserRouter({ children }: { children: React.ReactNode }) {
  const [location, setLocation] = useState<LocationSnapshot>(() => ({
    pathname: window.location.pathname || '/',
    search: window.location.search,
    hash: window.location.hash,
  }));
  useEffect(() => {
    const onPopState = () => setLocation({
      pathname: window.location.pathname || '/',
      search: window.location.search,
      hash: window.location.hash,
    });
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  const navigate = useCallback((to: string, options?: NavigateOptions) => {
    const next = parseLocation(to);
    if (options?.replace) window.history.replaceState(null, '', to);
    else window.history.pushState(null, '', to);
    setLocation(next);
  }, []);
  return <RouterContext.Provider value={{ location, navigate }}>{children}</RouterContext.Provider>;
}

export function MemoryRouter({
  children,
  initialEntries = ['/'],
}: {
  children: React.ReactNode;
  initialEntries?: string[];
}) {
  const [location, setLocation] = useState<LocationSnapshot>(
    () => parseLocation(initialEntries[0] ?? '/'),
  );
  const navigate = useCallback((to: string) => setLocation(parseLocation(to)), []);
  return <RouterContext.Provider value={{ location, navigate }}>{children}</RouterContext.Provider>;
}

export function useLocation(): LocationSnapshot {
  return useRouter().location;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function useSearchParams(): [
  URLSearchParams,
  (next: URLSearchParams | Record<string, string> | string) => void,
] {
  const { location, navigate } = useRouter();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const setParams = useCallback((next: URLSearchParams | Record<string, string> | string) => {
    const value = typeof next === 'string'
      ? new URLSearchParams(next)
      : next instanceof URLSearchParams
        ? next
        : new URLSearchParams(next);
    const query = value.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
  }, [location.hash, location.pathname, navigate]);
  return [params, setParams];
}

export function useParams<T extends Record<string, string | undefined> = Record<string, string>>() {
  return useContext(ParamsContext) as T;
}

type LinkProps = Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  to: string;
};

export function Link({ to, onClick, target, children, ...props }: LinkProps) {
  const { navigate } = useRouter();
  return (
    <a href={to} target={target} {...props} onClick={event => {
      onClick?.(event);
      if (
        event.defaultPrevented || target === '_blank' || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
      ) return;
      event.preventDefault();
      navigate(to);
    }}>
      {children}
    </a>
  );
}

type NavState = { isActive: boolean };
type NavLinkProps = Omit<LinkProps, 'className' | 'children'> & {
  end?: boolean;
  className?: string | ((state: NavState) => string);
  children?: React.ReactNode | ((state: NavState) => React.ReactNode);
};

export function NavLink({
  to, end = false, className, children, ...props
}: NavLinkProps) {
  const { pathname } = useLocation();
  const targetPath = parseLocation(to).pathname;
  const isActive = end
    ? pathname === targetPath
    : pathname === targetPath || pathname.startsWith(`${targetPath}/`);
  const state = { isActive };
  return (
    <Link to={to} {...props}
      className={typeof className === 'function' ? className(state) : className}
      aria-current={isActive ? 'page' : undefined}>
      {typeof children === 'function' ? children(state) : children}
    </Link>
  );
}

interface RouteProps {
  path: string;
  element: React.ReactNode;
}

export const Route: React.FC<RouteProps> = () => null;

function matchRoute(pattern: string, pathname: string): Record<string, string> | null {
  const patternParts = pattern.split('/').filter(Boolean);
  const pathParts = pathname.split('/').filter(Boolean);
  if (patternParts.length !== pathParts.length) return null;
  const params: Record<string, string> = {};
  for (let index = 0; index < patternParts.length; index += 1) {
    const expected = patternParts[index];
    const actual = pathParts[index];
    if (expected.startsWith(':')) {
      try {
        params[expected.slice(1)] = decodeURIComponent(actual);
      } catch {
        return null;
      }
    } else if (expected !== actual) {
      return null;
    }
  }
  return params;
}

export function Routes({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  for (const child of React.Children.toArray(children)) {
    if (!React.isValidElement<RouteProps>(child)) continue;
    const params = matchRoute(child.props.path, pathname);
    if (params !== null) {
      return <ParamsContext.Provider value={params}>{child.props.element}</ParamsContext.Provider>;
    }
  }
  return null;
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace }), [navigate, replace, to]);
  return null;
}
