import { mountUserRoutes } from "../src/routes/userRoutes";

export async function runUserServiceTest(): Promise<string> {
  return mountUserRoutes({
    name: "Ada",
    email: "ada@example.com",
  });
}
