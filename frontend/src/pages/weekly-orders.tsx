import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => {
  return {
    redirect: {
      destination: "/orders",
      permanent: false,
    },
  };
};

export default function WeeklyOrdersRedirectPage() {
  return null;
}
