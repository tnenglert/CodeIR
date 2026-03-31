import { UserProfile } from "../types/domain";

export function normalizeUser(profile: UserProfile): UserProfile {
  if (profile.name.trim().length === 0) {
    return { ...profile, name: "anonymous" };
  }

  return {
    ...profile,
    name: profile.name.trim(),
    email: profile.email.toLowerCase(),
  };
}

export const formatHandle = (profile: UserProfile): string => {
  return `${profile.name.toLowerCase()}-${profile.email.split("@")[0]}`;
};
