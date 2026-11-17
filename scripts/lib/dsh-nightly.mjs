// Purpose-limited daily publishing grant, independent of scientific delegation.
const PROJECT = '/home/riveruning/biologymodel_agent';
const REPOSITORY = 'riveruning/Biomodeling';

export function nightlyEnvironmentValid(env = process.env, now = Date.now()) {
  const date = env.NIGHTLY_DATE;
  const until = Date.parse(env.NIGHTLY_UNTIL || '');
  return env.NIGHTLY_REPOSITORY === REPOSITORY
    && typeof date === 'string'
    && /^\d{4}-\d{2}-\d{2}$/.test(date)
    && Number.isFinite(Date.parse(date))
    && new Date(date).toISOString().slice(0, 10) === date
    && Number.isFinite(until) && until > now && until - now <= 3 * 3600000;
}

export function nightlyTaskValid(task, workspace, env = process.env, now = Date.now()) {
  return workspace === PROJECT
    && task.delegation?.purpose === 'nightly_release'
    && task.delegation.workspace === PROJECT
    && task.delegation.until === env.NIGHTLY_UNTIL
    && task.id === `dsh-nightly-release-${env.NIGHTLY_DATE?.replaceAll('-', '')}`
    && nightlyEnvironmentValid(env, now);
}

export function delegationEnvironment(task) {
  if (task.delegation?.purpose === 'nightly_release') {
    return Object.fromEntries(['NIGHTLY_REPOSITORY', 'NIGHTLY_DATE', 'NIGHTLY_UNTIL']
      .map(key => [key, process.env[key]]));
  }
  return task.delegation ? { BIOMODEL_DSH_DELEGATION_UNTIL: task.delegation.until } : undefined;
}
