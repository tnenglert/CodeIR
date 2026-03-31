import { loadUser } from "../data/userRepo";
import { UserService } from "../services/userService";

export type RouterLike = {
  get(path: string, handler: (id: string) => Promise<string>): void;
};

export function registerUserRoutes(router: RouterLike): void {
  router.get("/users/:id", async (id: string) => {
    const service = new UserService();
    return service.saveUser(await loadUser(id));
  });
}
