import { UserStatus, type User } from "../models/user";

export async function loadUser(id: string): Promise<User> {
  return {
    id,
    name: "Ada",
    status: UserStatus.Active
  };
}

export function persistUser(user: User): string {
  return user.id;
}
