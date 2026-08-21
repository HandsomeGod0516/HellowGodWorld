/**
 * StreamingContent 元件
 *
 * 流式內容顯示
 */

interface StreamingContentProps {
  content: string;
  isStreaming: boolean;
}

export function StreamingContent({ content, isStreaming }: StreamingContentProps) {
  return (
    <div className="chat-text">
      <span className="whitespace-pre-wrap">{content}</span>
      {isStreaming && <span className="streaming-cursor" />}
    </div>
  );
}
