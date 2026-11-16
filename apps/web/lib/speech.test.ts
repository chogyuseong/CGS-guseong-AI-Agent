import { describe, expect, it } from "vitest";
import {
  collectFinalText,
  findSpeechRecognition,
  joinTranscript,
  speechErrorMessage,
} from "./speech";

function result(transcript: string, isFinal: boolean) {
  return Object.assign([{ transcript }], { isFinal });
}

describe("findSpeechRecognition", () => {
  it("표준 이름을 먼저 쓰고, 없으면 webkit 접두사, 둘 다 없으면 null", () => {
    class Standard {}
    class Webkit {}
    expect(
      findSpeechRecognition({ SpeechRecognition: Standard, webkitSpeechRecognition: Webkit }),
    ).toBe(Standard);
    expect(findSpeechRecognition({ webkitSpeechRecognition: Webkit })).toBe(Webkit);
    expect(findSpeechRecognition({})).toBeNull();
  });
});

describe("joinTranscript", () => {
  it("빈 입력창에는 그대로, 글이 있으면 한 칸 띄워 붙인다", () => {
    expect(joinTranscript("", " 안녕 ", 100)).toBe("안녕");
    expect(joinTranscript("내일", "일정", 100)).toBe("내일 일정");
    expect(joinTranscript("내일 ", "일정", 100)).toBe("내일 일정");
    expect(joinTranscript("내일\n", "일정", 100)).toBe("내일\n일정");
  });

  it("빈 결과는 무시하고, 최대 길이를 넘기지 않는다", () => {
    expect(joinTranscript("그대로", "  ", 100)).toBe("그대로");
    expect(joinTranscript("12345", "678", 7)).toBe("12345 6");
  });
});

describe("collectFinalText", () => {
  it("resultIndex부터 확정된 결과만 모은다", () => {
    const results = [result("이미 받은 말", true), result("내일", true), result("중간", false)];
    expect(collectFinalText({ resultIndex: 1, results })).toBe("내일");
  });
});

describe("speechErrorMessage", () => {
  it("직접 취소한 경우에는 안내하지 않는다", () => {
    expect(speechErrorMessage("aborted", true)).toBeNull();
  });

  it("권한 거부는 https가 아니면 주소 안내, https면 설정 안내", () => {
    expect(speechErrorMessage("not-allowed", false)).toContain("https");
    expect(speechErrorMessage("not-allowed", true)).toContain("마이크 권한");
    expect(speechErrorMessage("service-not-allowed", true)).toContain("마이크 권한");
  });

  it("무음·마이크 없음·알 수 없는 오류를 한국어로 안내한다", () => {
    expect(speechErrorMessage("no-speech", true)).toContain("말소리가 들리지 않았습니다");
    expect(speechErrorMessage("audio-capture", true)).toContain("마이크를 찾지 못했습니다");
    expect(speechErrorMessage("something-new", true)).toContain("음성 인식");
  });
});
