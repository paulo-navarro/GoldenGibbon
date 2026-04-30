---
description: "Use when writing React components, hooks, stores, or pages in frontend/src/. Covers GoldenGibbon frontend conventions: MUI imports, React Query v5, Zustand, TypeScript patterns, and file organization."
applyTo: "frontend/src/**/*.tsx, frontend/src/**/*.ts"
---

# GoldenGibbon Frontend Conventions

## MUI Imports — Mandatory
Always import MUI components by their direct path, never from the barrel:
```tsx
// ✅ Correct
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import LinearProgress from '@mui/material/LinearProgress';
import TablePagination from '@mui/material/TablePagination';
import ArrowDropUpIcon from '@mui/icons-material/ArrowDropUp';

// ❌ Wrong — barrel imports hurt tree-shaking
import { Box, Typography } from '@mui/material';
```

## TypeScript — Props and Types
```tsx
// Always type props with interface
interface MyComponentProps {
  symbol: string;
  value: number;
  onSelect?: (symbol: string) => void;
}

// Dictionaries
const prices: Record<string, number> = {};

// Optional fields
interface State {
  error: string | null;
  loading: boolean;
}
```

## React Query v5 (useQuery / useMutation)
```tsx
import { useQuery, useMutation } from '@tanstack/react-query';

// useQuery — always provide queryKey and queryFn
const { data, isLoading, error } = useQuery({
  queryKey: ['portfolio', symbol],
  queryFn: () => fetchPortfolio(symbol),
  refetchInterval: 30_000,  // 30s polling if needed
});

// useMutation
const { mutate, isPending } = useMutation({
  mutationFn: (payload: OrderPayload) => createOrder(payload),
  onSuccess: () => { /* ... */ },
});
```

## Zustand Stores
- Store files → `frontend/src/stores/<name>Store.ts`
- Reference: `frontend/src/stores/marketStore.ts`
```ts
import { create } from 'zustand';

interface MyState { ... }
interface MyActions { ... }

export const useMyStore = create<MyState & MyActions>((set) => ({
  // initial state
  // actions using set()
}));
```

## Custom Hooks
- Hook files → `frontend/src/hooks/use<Name>.ts`
- Wrap React Query calls + store subscriptions into hooks
- Never call `fetch` directly in components — use hooks

## File Organization
```
frontend/src/
  components/   ← reusable UI components
  pages/        ← route-level page components
  hooks/        ← custom hooks (useXxx.ts)
  stores/       ← Zustand stores (xxxStore.ts)
  api/          ← API call functions
  types/        ← TypeScript interfaces/types
  layouts/      ← layout wrappers (AppLayout, etc.)
```

## React Router v7
```tsx
// Link component
import { Link, useNavigate } from 'react-router-dom';

// Navigate programmatically
const navigate = useNavigate();
navigate('/prices');
```

## Component Pattern
```tsx
export interface MyComponentProps {
  symbol: string;
}

export default function MyComponent({ symbol }: MyComponentProps) {
  // hooks at top
  // conditional returns
  // JSX
}
```
