// API 호출 클라이언트. 손으로 쓰는 파일은 이것 하나뿐이고, 타입(schema.d.ts)은 pnpm gen:api가 만든다.
// schema.d.ts는 FastAPI의 OpenAPI 문서에서 자동 생성되므로 직접 고치지 않는다.
import createClient from "openapi-fetch";
// `import type`은 타입만 가져오고 실행 코드에는 남지 않는다. (Python의 `if TYPE_CHECKING:` import와 비슷)
import type { paths } from "./schema";

// createClient<paths>: <> 안은 제네릭(타입 인자)이다. 이 클라이언트가 어떤 경로와 응답 모양을 갖는지 알려 준다.
// (Django REST framework의 Serializer 정의를 호출하는 쪽에서도 그대로 쓰는 느낌)
// baseUrl을 비워 두면 브라우저가 지금 주소(같은 출처)로 /api/... 를 부르고, Next rewrites가 FastAPI로 넘긴다.
export const api = createClient<paths>({
  baseUrl: "",
  // 세션 쿠키를 함께 보낸다. 같은 출처라 기본값으로도 보내지만 의도를 분명히 적는다.
  credentials: "same-origin",
});
