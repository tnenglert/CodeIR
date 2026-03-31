import { AuditLabels, UserId, UserProfile } from "../types/domain";

export async function writeAudit(userId: UserId): Promise<string> {
  return AuditLabels.userSaved(userId);
}

export async function persistUser(profile: UserProfile): Promise<UserId> {
  const auditLabel = await writeAudit(profile.email);
  return auditLabel;
}
