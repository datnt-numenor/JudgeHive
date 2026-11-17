import React from "react";

// React escapes every token; source code is never inserted as HTML.
export function CodeBlock({ code = "" }) {
  return <div className="code-block">{code.split("\n").map((line, i) => <div className="code-line" key={i}><span className="line-number">{i + 1}</span><code>{line.split(/("[^"\n]*"|'[^'\n]*'|\b(?:def|class|if|else|for|while|return|import|from|int|str|range|map|print|input|public|static|void|include|using|namespace|std)\b)/g).map((token, j) => <span key={j} className={/^["']/.test(token) ? "syntax-string" : /^(print|input)$/.test(token) ? "syntax-call" : /^(def|class|if|else|for|while|return|import|from|int|str|range|map|public|static|void|include|using|namespace|std)$/.test(token) ? "syntax-keyword" : undefined}>{token}</span>) || " "}</code></div>)}</div>;
}
