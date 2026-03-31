import { UserProfile } from "../types/domain";
import { createService } from "../services/userService";

export async function mountUserRoutes(profile: UserProfile): Promise<string> {
  const service = createService();
  return service.registerUser(profile);
}
