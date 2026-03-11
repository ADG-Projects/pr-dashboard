/** Deterministic color assignment for repos based on full_name hash. */

const REPO_COLORS = [
  '#61dafb', // cyan
  '#a78bfa', // purple
  '#fb923c', // orange
  '#4ade80', // green
  '#f472b6', // pink
  '#facc15', // yellow
  '#38bdf8', // sky
  '#c084fc', // violet
  '#34d399', // emerald
  '#fb7185', // rose
];

export function repoColor(fullName: string): string {
  let hash = 0;
  for (const c of fullName) hash = ((hash << 5) - hash + c.charCodeAt(0)) | 0;
  return REPO_COLORS[Math.abs(hash) % REPO_COLORS.length];
}
