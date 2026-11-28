import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <p className="text-sm font-medium text-accent">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-sm text-muted">That page doesn&apos;t exist.</p>
      <Link href="/" className="btn-secondary mt-6">
        Back to Discover
      </Link>
    </div>
  );
}
