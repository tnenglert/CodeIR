import type { User } from "../models/user";

export const DEFAULT_PREFIX = "user";

export function formatUser(user: User): string {
  return `${DEFAULT_PREFIX}:${user.id}`;
}

export function formatLegacy(user: User): string {
  return formatUser(user).toUpperCase();
}
