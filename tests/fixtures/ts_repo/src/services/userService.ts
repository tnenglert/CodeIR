import type { User } from "../models/user";
import { loadUser, persistUser } from "../data/userRepo";
import { formatUser } from "../utils/format";

export class UserService {
  async saveUser(user: User): Promise<string> {
    const preview = this.formatUser(user);
    persistUser(user);
    return preview;
  }

  formatUser(user: User): string {
    return formatUser(user);
  }
}

export const loadPreview = async (id: string): Promise<string> => {
  const user = await loadUser(id);
  return formatUser(user);
};
