import { DEFAULT_ROLE, UserId, UserProfile } from "../types/domain";
import { persistUser } from "./persistence";
import { formatHandle, normalizeUser } from "../utils/normalize";

export class UserService {
  async registerUser(profile: UserProfile): Promise<UserId> {
    const normalized = normalizeUser(profile);
    const handle = formatHandle(normalized);

    if (handle.length === 0) {
      throw new Error("empty handle");
    }

    return persistUser({
      ...normalized,
      name: `${normalized.name}:${DEFAULT_ROLE}`,
    });
  }

  formatSummary(profile: UserProfile): string {
    return formatHandle(profile);
  }
}

export const createService = (): UserService => new UserService();
