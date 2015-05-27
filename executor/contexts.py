# Programmer friendly subprocess wrapper.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: May 27, 2015
# URL: https://executor.readthedocs.org

r"""
The :mod:`executor.contexts` module
==================================

The :mod:`~executor.contexts` module defines the :class:`LocalContext` and
:class:`RemoteContext` classes. Both of these classes support the same API for
executing external commands (they are simple wrappers for
:class:`.ExternalCommand` and :class:`.RemoteCommand`). This allows you to
script interaction with external commands in Python and perform that
interaction on your local system or on a remote system (over SSH) using the
exact same Python code. `Dependency injection`_ on steroids anyone? :-)

Here's a simple example:

.. code-block:: python

   from executor.contexts import LocalContext, RemoteContext
   from humanfriendly import format_timespan

   def details_about_system(context):
       return "\n".join([
           "Information about %s:" % context,
           " - Host name: %s" % context.capture('hostname', '--fqdn'),
           " - Uptime: %s" % format_timespan(float(context.capture('cat', '/proc/uptime').split()[0])),
       ])

   print(details_about_system(LocalContext()))

   # Information about local system (peter-macbook):
   #  - Host name: peter-macbook
   #  - Uptime: 1 week, 3 days and 10 hours

   print(details_about_system(RemoteContext('file-server')))

   # Information about remote system (file-server):
   #  - Host name: file-server
   #  - Uptime: 18 weeks, 3 days and 4 hours

Whether this functionality looks exciting or horrible I'll leave up to your
judgment. I created it because I'm always building "tools that help me build
tools" and this functionality enables me to *very rapidly* prototype system
integration tools developed using Python:

**During development:**
 I *write* code on my workstation which I prefer because of the "rich editing
 environment" but I *run* the code against a remote system over SSH (a backup
 server, database server, hypervisor, mail server, etc.).

**In production:**
 I change one line of code to inject a :class:`LocalContext` object instead of
 a :class:`RemoteContext` object, I install the `executor` package and the code
 I wrote on the remote system and I'm done!

.. _Dependency injection: http://en.wikipedia.org/wiki/Dependency_injection
"""

# Standard library modules.
import logging
import socket

# Modules included in our package.
from executor import DEFAULT_SHELL, ExternalCommand
from executor.ssh.client import RemoteCommand, SSH_PROGRAM_NAME

# Initialize a logger.
logger = logging.getLogger(__name__)


class AbstractContext(object):

    """
    Abstract base class for shared logic of all context classes.

    The most useful methods of this class are :func:`execute()`,
    :func:`capture()`, :func:`cleanup()` and :func:`start_interactive_shell()`.
    """

    def __init__(self, **options):
        """
        Construct an :class:`AbstractContext` object.

        :param options: Any keyword arguments are passed on to all
                        :class:`.ExternalCommand` objects constructed
                        using this context.

        .. note:: This constructor must be called by subclasses.
        """
        self.options = options
        self.undo_stack = []

    def prepare_command(self, command, options):
        """
        Construct an :class:`.ExternalCommand` object based on the current context.

        :param command: A tuple of strings (the positional arguments to the
                        constructor of :class:`.ExternalCommand`).
        :param options: A dictionary (the keyword arguments to the constructor
                        of :class:`.ExternalCommand`).
        :returns: Expected to return an :class:`.ExternalCommand` object *that
                  hasn't been started yet*.

        .. note:: This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    def prepare_interactive_shell(self, options):
        """
        Construct an :class:`.ExternalCommand` object that starts an interactive shell.

        :param options: A dictionary (the keyword arguments to the constructor
                        of :class:`.ExternalCommand`).
        :returns: Expected to return an :class:`.ExternalCommand` object *that
                  hasn't been started yet*.

        .. note:: This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    def merge_options(self, overrides):
        """
        This method can be used by subclasses to easily merge default options and overrides.

        :param overrides: A dictionary with any keyword arguments given to
                          :func:`execute()` or :func:`start_interactive_shell()`.
        :returns: The dictionary with overrides, but any keyword arguments
                  given to the constructor of :class:`AbstractContext` that are
                  not set in the overrides are set to the value of the
                  constructor argument.
        """
        for name, value in self.options.items():
            overrides.setdefault(name, value)
        return overrides

    def execute(self, *command, **options):
        """
        Execute an external command in the current context.

        :param command: All positional arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :param options: All keyword arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :returns: The :class:`.ExternalCommand` object.

        .. note:: After constructing an :class:`.ExternalCommand` object this
                  method calls :func:`~executor.ExternalCommand.start()` on the
                  command before returning it to the caller, so by the time the
                  caller gets the command object a synchronous command will
                  have already ended. Asynchronous commands don't have this
                  limitation of course.
        """
        cmd = self.prepare_command(command, options)
        cmd.start()
        return cmd

    def capture(self, *command, **options):
        """
        Execute an external command in the current context and capture its output.

        :param command: All positional arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :param options: All keyword arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :returns: The value of :attr:`.ExternalCommand.output`.
        """
        options['capture'] = True
        cmd = self.prepare_command(command, options)
        cmd.start()
        return cmd.output

    def cleanup(self, *command, **options):
        """
        Register an external command to be called before the context ends.

        :param command: All positional arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :param options: All keyword arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.

        This method registers *the intent* to run an external command in the
        current context before the context ends. To actually run the command
        you need to use the context object as a context manager (using the
        :keyword:`with` statement).

        The last command that is registered is the first one to be executed.
        This gives the equivalent functionality of a deeply nested
        :keyword:`try` / :keyword:`finally` structure without actually needing
        to write such ugly code :-).

        .. warning:: If a cleanup command fails and raises an exception no
                     further cleanup commands are executed. If you don't care
                     if a specific cleanup command reports an error, set its
                     :attr:`~.ExternalCommand.check` property to
                     :data:`False`.
        """
        self.undo_stack.append((command, options))

    def start_interactive_shell(self, **options):
        """
        Start an interactive shell in the current context.

        :param options: All keyword arguments are passed on to the
                        constructor of the :class:`.ExternalCommand` class.
        :returns: The :class:`.ExternalCommand` object.

        .. note:: After constructing an :class:`.ExternalCommand` object this
                  method calls :func:`~executor.ExternalCommand.start()` on the
                  command before returning it to the caller, so by the time the
                  caller gets the command object a synchronous command will
                  have already ended. Asynchronous commands don't have this
                  limitation of course.
        """
        cmd = self.prepare_interactive_shell(options)
        cmd.start()
        return cmd

    def __enter__(self):
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        while self.undo_stack:
            # FYI: There's no explicit error handling inside this error handler
            # (if one cleanup command fails, should the next one be run?). The
            # reason is that I simply haven't decided about correct semantics
            # here...
            command, options = self.undo_stack.pop()
            self.execute(*command, **options)


class LocalContext(AbstractContext):

    """Context for executing commands on the local system."""

    def prepare_command(self, command, options):
        return ExternalCommand(*command, **self.merge_options(options))

    def prepare_interactive_shell(self, options):
        return ExternalCommand(DEFAULT_SHELL, **self.merge_options(options))

    def __str__(self):
        return "local system (%s)" % socket.gethostname()

class RemoteContext(AbstractContext):

    """Context for executing commands on a remote system over SSH."""

    def __init__(self, ssh_alias, **options):
        """
        Construct a :class:`RemoteContext` object.

        :param ssh_alias: The SSH alias of the remote system (a string).
        :param options: Refer to :func:`AbstractContext.__init__()`.
        """
        super(RemoteContext, self).__init__(**options)
        self.ssh_alias = ssh_alias

    def prepare_command(self, command, options):
        return RemoteCommand(self.ssh_alias, *command, **self.merge_options(options))

    def prepare_interactive_shell(self, options):
        # Force pseudo-tty allocation using `ssh -t', but take care not to
        # destroy custom `ssh_command' values provided by callers.
        options = self.merge_options(options)
        ssh_command = options.setdefault('ssh_command', [SSH_PROGRAM_NAME])
        if '-t' not in ssh_command:
            ssh_command.append('-t')
        return RemoteCommand(self.ssh_alias, DEFAULT_SHELL, **options)

    def __str__(self):
        return "remote system (%s)" % self.ssh_alias
