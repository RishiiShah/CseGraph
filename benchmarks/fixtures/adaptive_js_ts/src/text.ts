export function normalizeName(value: string): string {
  return value.trim().toLowerCase();
}

export function formatLabel(value: string): string {
  return normalizeName(value).replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function slugify(value: string): string {
  return normalizeName(value).replace(/\s+/g, "-");
}
