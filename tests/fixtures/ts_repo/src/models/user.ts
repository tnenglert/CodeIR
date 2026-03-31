export interface User {
  id: string;
  name: string;
  status: UserStatus;
}

export type UserRecord = Record<string, User>;

export enum UserStatus {
  Active = "active",
  Disabled = "disabled"
}
