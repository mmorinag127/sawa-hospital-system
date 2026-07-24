import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  publicPage?: boolean;
};

export default function PageTemplate({ children, publicPage = false }: Props) {
  return (
    <div
      className={`sawa-page-template${publicPage ? " sawa-page-template--public" : ""}`}
      data-page-template="sawa"
    >
      {children}
    </div>
  );
}
