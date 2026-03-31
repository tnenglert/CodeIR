import { loadPreview, UserService } from "../src/services/userService";

test("formats preview", async () => {
  const service = new UserService();
  await loadPreview("1");
  return service.formatUser({
    id: "1",
    name: "A",
    status: "active" as never
  });
});
