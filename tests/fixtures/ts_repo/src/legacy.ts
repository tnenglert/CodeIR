import { formatLegacy } from "./utils/format";

namespace LegacyUser {
  export function normalize(): string {
    return formatLegacy({
      id: "42",
      name: "Ada",
      status: "active" as never
    });
  }
}

export { LegacyUser };
