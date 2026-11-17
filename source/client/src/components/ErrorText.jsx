import React from "react";

export function ErrorText({ message }) {
  return message ? (
    <p role="alert" className="error">
      {message}
    </p>
  ) : null;
}
