import { ArrowUp } from "lucide-react";
import { useEffect, useRef } from "react";

interface ComposerProps {
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}

export function Composer({ value, busy, onChange, onSubmit }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 144)}px`;
  }, [value]);

  return (
    <div className="composer-region">
      <form
        className="composer glass-surface"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <label className="sr-only" htmlFor="coach-prompt">
          输入你想理解的问题
        </label>
        <textarea
          ref={textareaRef}
          id="coach-prompt"
          value={value}
          rows={1}
          maxLength={4000}
          disabled={busy}
          placeholder="说说你卡在哪，或直接问一个具体问题…"
          aria-describedby="composer-hint"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <div className="composer-footer">
          <button
            className="send-button"
            type="submit"
            disabled={busy || !value.trim()}
            aria-label={busy ? "正在生成讲解" : "发送问题"}
          >
            <ArrowUp aria-hidden="true" size={20} strokeWidth={2.2} />
          </button>
        </div>
      </form>
      <p className="composer-hint" id="composer-hint">
        <span>Enter 发送 · Shift + Enter 换行</span>
      </p>
    </div>
  );
}
