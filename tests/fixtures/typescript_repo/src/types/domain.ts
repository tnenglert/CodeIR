export interface UserProfile {
  name: string;
  email: string;
}

export type UserId = string;

export enum Role {
  Member = "member",
  Admin = "admin",
}

export namespace AuditLabels {
  export function userSaved(id: UserId): string {
    return `saved:${id}`;
  }
}

export const DEFAULT_ROLE = Role.Member;
