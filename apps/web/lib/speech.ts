// 음성 입력: 브라우저의 SpeechRecognition(Web Speech API)으로 말을 글자로 바꿔 입력창에 넣는다.
// 이 앱은 음성을 서버로 보내지 않는다. 인식은 브라우저가 맡는다(크롬은 구글 서버에서 인식한다).
//
// [브라우저 전용 API를 TypeScript에서 다루는 법]
// 1) 'use client': Next.js는 컴포넌트를 먼저 서버(Node.js)에서 HTML로 그린다. 서버에는 window도 마이크도 없다.
//    (Django로 치면 템플릿을 렌더링하는 순간에는 사용자의 브라우저가 없는 것과 같다.)
//    그래서 이 훅은 'use client'가 붙은 컴포넌트(ChatInput)에서만 쓰고, window는 클릭 처리나 effect처럼
//    브라우저에서만 도는 코드 안에서만 읽는다. 이 파일 자체는 훅·함수 모음이라 지시문이 필요 없다.
// 2) 타입 선언: TypeScript에 딸린 브라우저 타입(lib.dom.d.ts)에는 SpeechRecognition이 없다.
//    아직 정식 표준이 아니고 브라우저마다 이름(webkit 접두사)이 달라서다. 그래서 이 앱이 쓰는 부분의 모양만
//    아래에 직접 적었다(Python의 Protocol처럼 "이런 속성과 메서드가 있는 객체"라는 약속).
//    전역 타입에 끼워 넣는 `declare global { interface Window { ... } }` 방식도 있지만, 그러면 모든 파일에서
//    window.SpeechRecognition이 늘 있는 것처럼 보이므로, 여기서는 "없을 수도 있음"을 드러내는 지역 타입을 쓴다.
// 3) 하이드레이션: 서버가 그린 HTML과 브라우저의 첫 화면이 다르면 React가 경고하고 화면을 다시 그린다.
//    지원 여부는 useSyncExternalStore로 "서버에서는 false, 브라우저에서는 실제 값"을 주어 첫 화면을 서버와 맞춘다.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export type SpeechAlternativeLike = { transcript: string };
export type SpeechResultLike = ArrayLike<SpeechAlternativeLike> & { isFinal: boolean };
export type SpeechResultEventLike = { resultIndex: number; results: ArrayLike<SpeechResultLike> };
export type SpeechErrorEventLike = { error: string };

// 브라우저의 SpeechRecognition 객체 중 이 앱이 쓰는 부분만. 실제 객체도 이 모양을 만족한다.
export type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechResultEventLike) => void) | null;
  onerror: ((event: SpeechErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechWindow = {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

const NO_SPEECH = "말소리가 들리지 않았습니다. 마이크 버튼을 다시 누르고 말씀해 주세요.";
const GENERIC_ERROR = "음성 인식이 되지 않았습니다. 잠시 후 다시 시도하거나 글자로 입력해 주세요.";
const ERROR_MESSAGES: Record<string, string> = {
  "no-speech": NO_SPEECH,
  "audio-capture": "마이크를 찾지 못했습니다. 마이크가 연결되어 있는지 확인해 주세요.",
  network: "음성 인식에 인터넷 연결이 필요합니다. 연결을 확인한 뒤 다시 시도해 주세요.",
  "language-not-supported":
    "이 브라우저는 한국어 음성 인식을 지원하지 않습니다. 글자로 입력해 주세요.",
};
const DENIED =
  "마이크 권한이 거부되어 있습니다. 브라우저(아이폰은 설정 → Safari) 설정에서 이 사이트의 마이크를 허용해 주세요.";
const INSECURE = "마이크는 https 주소(또는 이 PC의 localhost)로 열었을 때만 쓸 수 있습니다.";

// 표준 이름이 먼저, 없으면 크롬·사파리의 webkit 접두사. 둘 다 없으면 미지원(null).
export function findSpeechRecognition(win: object): SpeechRecognitionConstructor | null {
  const candidate = win as SpeechWindow;
  return candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition ?? null;
}

// 오류 코드(브라우저가 주는 영어 문자열)를 화면 문구로. "aborted"는 앱이 직접 취소한 것이라 안내하지 않는다.
export function speechErrorMessage(code: string, secureContext: boolean): string | null {
  if (code === "aborted") return null;
  if (code === "not-allowed" || code === "service-not-allowed") {
    return secureContext ? DENIED : INSECURE;
  }
  return ERROR_MESSAGES[code] ?? GENERIC_ERROR;
}

// 인식된 문장을 입력창 글 뒤에 붙인다. 이미 쓴 글은 지우지 않고, 사이에 공백이 없으면 한 칸 띄운다.
export function joinTranscript(current: string, addition: string, maxLength: number): string {
  const piece = addition.trim();
  if (!piece) return current;
  const separator = current === "" || /\s$/.test(current) ? "" : " ";
  return `${current}${separator}${piece}`.slice(0, maxLength);
}

// 이번 이벤트에서 새로 확정된 결과(resultIndex부터, isFinal만)의 첫 후보를 이어 붙인다.
export function collectFinalText(event: SpeechResultEventLike): string {
  let text = "";
  for (let index = event.resultIndex; index < event.results.length; index += 1) {
    const result = event.results[index];
    if (result?.isFinal) text += result[0]?.transcript ?? "";
  }
  return text.trim();
}

// 지원 여부는 바뀌지 않으므로 구독할 것이 없다.
function subscribeNothing() {
  return () => {};
}

export function useSpeechSupported(): boolean {
  return useSyncExternalStore(
    subscribeNothing,
    () => findSpeechRecognition(window) !== null,
    () => false, // 서버에서 그릴 때와 하이드레이션 첫 화면: 버튼 없음
  );
}

// 한 번의 듣기. heard: 결과를 받았는지, failed: 오류 안내를 띄웠는지, stopped: 사용자가 멈췄는지.
type Session = {
  recognition: SpeechRecognitionLike;
  heard: boolean;
  failed: boolean;
  stopped: boolean;
};

function detach(recognition: SpeechRecognitionLike) {
  recognition.onresult = null;
  recognition.onerror = null;
  recognition.onend = null;
}

function createRecognition(Recognition: SpeechRecognitionConstructor): SpeechRecognitionLike {
  const recognition = new Recognition();
  recognition.lang = "ko-KR";
  recognition.continuous = false; // 한 문장을 말하고 멈추면 끝난다
  recognition.interimResults = false; // 확정된 글자만 받는다
  recognition.maxAlternatives = 1;
  return recognition;
}

type SessionHandlers = {
  onText: (text: string) => void;
  onError: (message: string) => void;
  onEnd: (session: Session) => void;
};

// 이벤트를 연결하고 듣기를 시작한다. 시작하지 못하면(이미 듣는 중 등) 안내를 띄우고 null.
function startSession(
  Recognition: SpeechRecognitionConstructor,
  handlers: SessionHandlers,
): Session | null {
  const recognition = createRecognition(Recognition);
  const session: Session = { recognition, heard: false, failed: false, stopped: false };
  recognition.onresult = (event) => {
    const text = collectFinalText(event);
    if (!text) return;
    session.heard = true;
    handlers.onText(text);
  };
  recognition.onerror = (event) => {
    const message = speechErrorMessage(event.error, window.isSecureContext);
    if (!message) return;
    session.failed = true;
    handlers.onError(message);
  };
  recognition.onend = () => {
    detach(recognition);
    handlers.onEnd(session);
  };
  try {
    recognition.start();
    return session;
  } catch {
    detach(recognition);
    handlers.onError(GENERIC_ERROR);
    return null;
  }
}

export type SpeechInput = {
  supported: boolean;
  listening: boolean;
  error: string | null;
  toggle: () => void;
  cancel: () => void;
};

// 지금 듣는 중인 session과 상태. cancel: 결과를 버리고 바로 끈다(전송할 때, 화면을 떠날 때).
function useSpeechSession() {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<Session | null>(null);

  const cancel = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    sessionRef.current = null;
    detach(session.recognition); // 늦게 오는 결과·오류를 받지 않게 먼저 끊는다
    session.recognition.abort();
    setListening(false);
  }, []);

  // 화면을 떠나면(컴포넌트가 사라지면) 마이크를 끈다.
  useEffect(() => cancel, [cancel]);
  return { listening, setListening, error, setError, sessionRef, cancel };
}

// 마이크 버튼의 상태와 동작. 인식된 글은 onText로만 넘긴다(전송은 하지 않는다).
// toggle: 듣기 시작 / 멈춤(멈추면 그때까지 말한 것은 결과로 온다).
export function useSpeechInput(onText: (text: string) => void): SpeechInput {
  const supported = useSpeechSupported();
  const { listening, setListening, error, setError, sessionRef, cancel } = useSpeechSession();
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  });

  const handlers: SessionHandlers = {
    onText: (text) => onTextRef.current(text),
    onError: setError,
    // 오류도 결과도 없이 저절로 끝났으면 무음으로 본다.
    onEnd: (session) => {
      if (sessionRef.current === session) sessionRef.current = null;
      setListening(false);
      if (!session.heard && !session.failed && !session.stopped) setError(NO_SPEECH);
    },
  };

  function toggle() {
    const current = sessionRef.current;
    if (current) {
      current.stopped = true;
      current.recognition.stop();
      return;
    }
    const Recognition = findSpeechRecognition(window);
    if (!Recognition) return;
    setError(null);
    sessionRef.current = startSession(Recognition, handlers);
    setListening(sessionRef.current !== null);
  }

  return { supported, listening, error, toggle, cancel };
}
