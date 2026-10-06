import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: { destination: "/orders", permanent: false },
});

export default function WeeklyOrdersRedirectPage() {
  return null;
}
